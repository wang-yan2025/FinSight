"""财务指标计算引擎（阶段2·第二步之二；纯计算，无 UI、无 LLM）。

单位与口径总则（继承 field_mapping / quarterly 的实测结论）
==========================================================
- 金额单位统一为元：东财接口不随行附带单位字段，已在字段映射阶段按数值
  量级与公开年报交叉核对确认。本模块输入默认来自 standardize_statement
  输出，不另行假定或换算单位；比率类指标分子分母同单位、无量纲。
- 净利润（NETPROFIT，含少数股东损益）与归母净利润（PARENT_NETPROFIT，
  归母口径）是两个不同科目：函数形参显式命名 net_profit /
  parent_net_profit，禁止混用。
- 成长能力只做同比（相同报告期类型跨年对齐）。【严禁】对累计值直接做
  环比；单季口径必须先经 derive_single_quarters 还原（quarterly.py）。
- 累计值不保证单调（单季可为负，经营现金流尤甚），本模块不据此判定
  数据错误。
- 缺失输入、零分母、负分母及口径不成立的组合一律返回 None（标量）或
  置 NaN 并附 reason（序列），不猜测、不插补。

指标定义
========
盈利能力（流量比率；当期口径取决于传入数值本身是累计还是单季）
  毛利率     gross_margin          = (营业收入 − 营业成本) / 营业收入
  净利率     net_margin            = 净利润 / 营业收入（净利润口径）
偿债能力（时点值，来自资产负债表）
  资产负债率 debt_to_asset_ratio   = 总负债 / 总资产
  流动比率   current_ratio         = 流动资产 / 流动负债
营运能力（年化周转率，次/年；分子流量与平均余额的统计期间必须一致）
  累计口径（basis="cumulative"，默认）：
    应收账款周转率 = 营业收入(年初至报告期末累计) × 12 / 期间月数 / 平均应收账款
    存货周转率     = 营业成本(年初至报告期末累计) × 12 / 期间月数 / 平均存货
    平均余额 = (年初余额 + 当前期末余额) / 2；年初余额 = 上一年年报期末余额；
    期间月数 = Q1=3 / H1=6 / Q3=9 / FY=12（与累计流量的覆盖期间一致）。
  单季口径（basis="single_quarter"）：
    流量 = 单季值（经 derive_single_quarters 还原），期间月数 = 3；
    平均余额 = (上一季度末余额 + 当前季度末余额) / 2；
    Q1 的期初 = 上一年年报期末余额。
  缺任一端余额或流量 → 该期不计算并给出 reason。
  注：应收周转分子用营业收入（未拆分㻌销比例，教科书通用近似）；
  存货周转分子用营业成本（教科书口径）。
成长能力（同比，同期对齐；结果必须结合 status 列解读）
  营业收入同比 = (当期营业收入 − 上年同期) / |上年同期|
  净利润同比   = (当期净利润   − 上年同期) / |上年同期|（净利润口径）
  status 状态分类（见 yoy_status）：
    normal               上年同期 > 0 且当期 ≥ 0，常规同比
                         （当期 = 0 即同比 −100% 至盈亏平衡，语义清晰）
    profit_to_loss       由盈转亏（上年同期 > 0，当期 < 0）
    turn_to_profit       扭亏为盈（上年同期 < 0，当期 > 0）
    loss_narrowing       亏损收窄（上年同期 < 0，当期亏损缩小或收窄至 0）
    loss_widening        亏损扩大（上年同期 < 0，当期亏损扩大）
    undefined_prior_zero 上年同期为 0，增长率无定义（数值缺失，含双零）
    missing              当期或上年同期数据缺失
  上年同期为负时，数值 = 变动额 / |上年同期|，仅表示变动幅度相对于
  基数绝对值的比例；展示与备忘录引用时不得将扭亏为盈/由盈转亏/亏损
  收窄/亏损扩大直述为"同比增长/下降"，必须同时给出状态说明。
  当期由盈转亏（profit_to_loss）时数值 < −100%，同样必须结合状态解读。
"""
from __future__ import annotations

import math
from typing import Callable, Mapping

import pandas as pd

from finsight.finance.quarterly import derive_single_quarters, period_of, quarter_months


def _missing(*values) -> bool:
    """任一输入为 None 或 NaN → 视为缺失。"""
    return any(v is None or (isinstance(v, float) and math.isnan(v)) for v in values)


# ---------------- 标量核心（纯函数） ----------------

def gross_margin(revenue: float | None, operate_cost: float | None) -> float | None:
    """毛利率 = (营业收入 − 营业成本) / 营业收入。

    营业收入为 0 或负 → None（口径不成立）；成本高于收入时允许负值
    （真实毛亏）。单位：分子分母同单位，结果无量纲。
    """
    if _missing(revenue, operate_cost) or revenue <= 0:
        return None
    return (revenue - operate_cost) / revenue


def net_margin(net_profit: float | None, revenue: float | None) -> float | None:
    """净利率 = 净利润 / 营业收入（净利润 = NETPROFIT，含少数股东损益）。

    归母口径请勿混用本函数；营业收入为 0 或负 → None。
    亏损期净利率为负，属有效结果。
    """
    if _missing(net_profit, revenue) or revenue <= 0:
        return None
    return net_profit / revenue


def debt_to_asset_ratio(total_liabilities: float | None, total_assets: float | None) -> float | None:
    """资产负债率 = 总负债 / 总资产（时点值）。

    总资产为 0 或负、总负债为负 → None（异常/口径不成立）。
    """
    if _missing(total_liabilities, total_assets) or total_assets <= 0 or total_liabilities < 0:
        return None
    return total_liabilities / total_assets


def current_ratio(total_current_assets: float | None, total_current_liabilities: float | None) -> float | None:
    """流动比率 = 流动资产 / 流动负债（时点值）。

    流动负债为 0 或负 → None（分母口径不成立）；流动资产为负 → None。
    """
    if (_missing(total_current_assets, total_current_liabilities)
            or total_current_liabilities <= 0 or total_current_assets < 0):
        return None
    return total_current_assets / total_current_liabilities


def yoy_growth(current: float | None, prior: float | None) -> float | None:
    """同比增长率 = (当期 − 上年同期) / |上年同期|。

    上年同期为 0 → 无定义返回 None；为负 → 分母取绝对值（扭亏/亏损
    扩大情形可正确计算）。当期为 0 → -1.0，有效。
    【注意】上年同期为负（扭亏为盈/亏损收窄/亏损扩大）或当期由盈转亏
    时，数值不能按"普通增长/下降"解读，必须配合 yoy_status 的状态分类使用。
    """
    if _missing(current, prior) or prior == 0:
        return None
    return (current - prior) / abs(prior)


def qoq_series(std: pd.DataFrame, col: str) -> pd.DataFrame:
    """环比（QoQ）序列：单季口径，当季 vs 上一季。

    返回列：report_date, year, quarter, current_sq, prior_sq, qoq, status, reason。
    环比仅对单季值计算（先经 derive_single_quarters 还原）；
    Q1 的上一季为上年 Q4（跨年环比）。
    """
    from finsight.finance.quarterly import derive_single_quarters, period_of

    derived = derive_single_quarters(std, [col])
    sq_col = f"{col}__sq"
    # 建 {(y,q): 单季值} 映射
    sq: dict[tuple[int, int], float] = {}
    dates: dict[tuple[int, int], pd.Timestamp] = {}
    for _, r in derived.frame.iterrows():
        if pd.notna(r["year"]) and pd.notna(r[sq_col]):
            key = (int(r["year"]), int(r["quarter"]))
            sq[key] = float(r[sq_col])
            dates[key] = r["report_date"]

    rows = []
    for key in sorted(sq):
        y, q = key
        prev_key = (y, q - 1) if q > 1 else (y - 1, 4)
        cur = sq[key]
        prior = sq.get(prev_key)
        row = {
            "report_date": dates[key], "year": y, "quarter": q,
            "current_sq": cur,
            "prior_sq": prior if prior is not None else math.nan,
            "qoq": math.nan, "status": "", "reason": "",
        }
        if prior is None:
            row["reason"] = "上一季度单季值缺失"
        elif prior == 0:
            row["reason"] = "上一季度为 0，环比无定义"
        else:
            val = (cur - prior) / abs(prior)
            row["qoq"] = val
            row["status"] = "normal"
        rows.append(row)
    return pd.DataFrame(rows)


def yoy_status(current: float | None, prior: float | None) -> str:
    """同比状态分类：防止扭亏/由盈转亏/亏损收窄/亏损扩大被误读为普通增长。

    返回值：
    - "normal"               上年同期 > 0 且当期 ≥ 0，常规同比
                             （当期 = 0 即同比 −100% 至盈亏平衡，数值语义清晰）
    - "profit_to_loss"       由盈转亏：上年同期 > 0 且当期 < 0
    - "turn_to_profit"       扭亏为盈：上年同期 < 0 且当期 > 0
    - "loss_narrowing"       亏损收窄：上年同期 < 0 且当期亏损缩小（含收窄至 0）
    - "loss_widening"        亏损扩大：上年同期 < 0 且当期亏损扩大或持平
    - "undefined_prior_zero" 上年同期为 0，增长率无定义（含双零）
    - "missing"              当期或上年同期数据缺失
    """
    if _missing(current, prior):
        return "missing"
    if prior == 0:
        return "undefined_prior_zero"
    if prior > 0:
        return "profit_to_loss" if current < 0 else "normal"
    if current > 0:
        return "turn_to_profit"
    if current == 0 or abs(current) < abs(prior):
        return "loss_narrowing"
    return "loss_widening"


def turnover_annualized(flow: float | None, begin_balance: float | None,
                        end_balance: float | None, months: int | None) -> float | None:
    """年化周转率 = 期间流量 × 12 / 期间月数 / 平均余额（次/年）。

    平均余额 = (期初 + 期末) / 2。平均余额为 0 或负、流量为负、月数
    非正 → None（口径不成立）。
    """
    if _missing(flow, begin_balance, end_balance, months) or months <= 0:
        return None
    average = (begin_balance + end_balance) / 2
    if average <= 0 or flow < 0:
        return None
    return flow * 12 / months / average


# ---------------- 序列级（按报告期逐期计算并标注缺失原因） ----------------

def apply_indicator(std: pd.DataFrame, fn: Callable[..., float | None],
                    col_map: Mapping[str, str], value_name: str = "value") -> pd.DataFrame:
    """对标准化报表逐期调用标量指标函数。

    col_map: 指标函数形参名 -> 标准字段 code，如
    {"revenue": "revenue", "operate_cost": "operate_cost"}。
    返回列：report_date + 输入字段列 + value_name + reason。
    reason 为空串表示正常计算；输入缺失或口径不成立时给出说明。
    """
    rows: list[dict] = []
    for _, row in std.iterrows():
        inputs = {param: row[code] for param, code in col_map.items()}
        missing = [param for param, v in inputs.items() if pd.isna(v)]
        rec: dict = {"report_date": row["report_date"]}
        rec.update({code: inputs[param] for param, code in col_map.items()})
        if missing:
            rec[value_name] = math.nan
            rec["reason"] = "输入缺失: " + ",".join(missing)
        else:
            value = fn(**{k: float(v) for k, v in inputs.items()})
            rec[value_name] = value if value is not None else math.nan
            rec["reason"] = "" if value is not None else "计算不成立（零/负分母或异常输入）"
        rows.append(rec)
    return pd.DataFrame(rows)


def yoy_series(std: pd.DataFrame, col: str, basis: str = "cumulative") -> pd.DataFrame:
    """同比序列。basis: "cumulative"（累计同比）或 "single_quarter"（单季同比）。

    对齐规则：当期 (y, q) 与上年同期 (y-1, q) 比较——两者口径一致
    （同为累计或同为单季），符合同比定义；不存在任何累计值环比。
    返回列：report_date, year, quarter, basis, current_value, prior_value,
    yoy, status, reason。
    status 为 yoy_status 的分类结果：上年同期为负（扭亏为盈/亏损收窄/
    亏损扩大）或为 0 时，数值必须结合 status 解读，不得直述为普通增长。
    """
    if basis not in ("cumulative", "single_quarter"):
        raise ValueError("basis 必须是 'cumulative' 或 'single_quarter'")

    if basis == "single_quarter":
        derived = derive_single_quarters(std, [col])
        work = derived.frame
        src = f"{col}__sq"
        missing_current = "当期单季值缺失（单季还原未完成，见派生说明）"
    else:
        src = col
        periods = [period_of(d) for d in std["report_date"]]
        work = pd.DataFrame({
            "report_date": std["report_date"].to_numpy(),
            "year": [p[0] if p else math.nan for p in periods],
            "quarter": [p[1] if p else math.nan for p in periods],
            col: std[col].to_numpy(),
        })
        missing_current = "当期值缺失"

    values: dict[tuple[int, int], float] = {}
    for _, row in work.iterrows():
        if pd.notna(row["year"]) and pd.notna(row[src]):
            values[(int(row["year"]), int(row["quarter"]))] = float(row[src])

    out_rows: list[dict] = []
    for _, row in work.iterrows():
        current = row[src] if pd.notna(row[src]) else None
        prior: float | None = None
        if pd.notna(row["year"]) and current is not None:
            prior = values.get((int(row["year"]) - 1, int(row["quarter"])))
        rec = {
            "report_date": row["report_date"],
            "year": row["year"],
            "quarter": row["quarter"],
            "basis": basis,
            "current_value": row[src],
            "prior_value": prior if prior is not None else math.nan,
            "yoy": math.nan,
            "status": yoy_status(current, prior),
            "reason": "",
        }
        if pd.isna(row["year"]):
            rec["reason"] = "报告期非标准季末"
        elif pd.isna(row[src]):
            rec["reason"] = missing_current
        elif prior is None:
            rec["reason"] = "上年同期值缺失"
        elif prior == 0:
            rec["reason"] = "上年同期为 0，增长率无定义"
        else:
            rec["yoy"] = (float(row[src]) - prior) / abs(prior)
        out_rows.append(rec)
    return pd.DataFrame(out_rows)


def _turnover_series(std_balance: pd.DataFrame, std_flow: pd.DataFrame,
                     flow_col: str, balance_col: str, basis: str = "cumulative") -> pd.DataFrame:
    """年化周转率序列（内部通用实现，按（财年, 报告期）对齐余额与流量）。

    分子流量与平均余额的统计期间强制一致：
    - basis="cumulative"：流量 = 年初至报告期末累计值；
      平均余额 = (年初余额 + 当前期末余额)/2，年初余额 = 上一年年报期末，
      期间月数 = 3×季度序号（与累计流量覆盖期间一致）。
    - basis="single_quarter"：流量 = 单季值（经 derive_single_quarters 还原）；
      平均余额 = (上一季度末 + 当前季度末)/2（Q1 期初 = 上一年年报期末），
      期间月数 = 3。
    任一端余额或流量缺失 → 该期不计算并给出 reason。
    """
    if basis not in ("cumulative", "single_quarter"):
        raise ValueError("basis 必须是 'cumulative' 或 'single_quarter'")
    if flow_col not in std_flow.columns:
        raise KeyError(f"流量列 {flow_col!r} 不在流量报表中")
    if balance_col not in std_balance.columns:
        raise KeyError(f"余额列 {balance_col!r} 不在资产负债表中")

    balances: dict[tuple[int, int], float] = {}
    for _, row in std_balance.iterrows():
        p = period_of(row["report_date"])
        if p is not None and pd.notna(row[balance_col]):
            balances[p] = float(row[balance_col])

    if basis == "single_quarter":
        derived = derive_single_quarters(std_flow, [flow_col])
        sq_col = f"{flow_col}__sq"
        flows: dict[tuple[int, int], float] = {}
        for _, row in derived.frame.iterrows():
            if pd.notna(row["year"]) and pd.notna(row[sq_col]):
                flows[(int(row["year"]), int(row["quarter"]))] = float(row[sq_col])
        flow_missing_reason = "单季流量缺失（单季还原未完成）"
    else:
        flows = {}
        for _, row in std_flow.iterrows():
            p = period_of(row["report_date"])
            if p is not None and pd.notna(row[flow_col]):
                flows[p] = float(row[flow_col])
        flow_missing_reason = f"流量缺失（{flow_col} 累计值）"

    out_rows: list[dict] = []
    for _, row in std_flow.iterrows():
        p = period_of(row["report_date"])
        quarter = p[1] if p else None
        months = 3 if (p and basis == "single_quarter") else (quarter_months(quarter) if p else math.nan)
        rec = {
            "report_date": row["report_date"],
            "year": p[0] if p else math.nan,
            "quarter": quarter if quarter else math.nan,
            "basis": basis,
            "months": months,
            "begin_balance": math.nan,
            "end_balance": math.nan,
            "average_balance": math.nan,
            "flow": row[flow_col] if basis == "cumulative" else math.nan,
            "value": math.nan,
            "reason": "",
        }
        if p is None:
            rec["reason"] = "报告期非标准季末"
            out_rows.append(rec)
            continue
        year, quarter = p
        begin_fallback = False
        if basis == "single_quarter":
            flow = flows.get(p)
            if flow is not None:
                rec["flow"] = flow
            begin = (balances.get((year, quarter - 1)) if quarter > 1
                     else balances.get((year - 1, 4)))
            # 期初余额回退：Q1 无上年年报时，用当年 Q1 自身余额近似
            if begin is None and quarter == 1:
                begin = balances.get((year, 1))
                begin_fallback = begin is not None
            begin_missing_reason = "期初余额缺失（需上一季度末余额；Q1 需上一年年报期末余额）"
        else:
            flow = flows.get(p)
            begin = balances.get((year - 1, 4))  # 年初余额 = 上一年年报期末
            # 年初余额回退：无上年年报时，用当年 Q1 期末余额近似
            if begin is None:
                begin = balances.get((year, 1))
                begin_fallback = begin is not None
            begin_missing_reason = "期初余额缺失（年初余额需上一年年报期末余额）"
        end = balances.get(p)
        if flow is None:
            rec["reason"] = flow_missing_reason
        elif end is None:
            rec["reason"] = "期末余额缺失"
        elif begin is None:
            rec["reason"] = begin_missing_reason
        else:
            rec["begin_balance"] = begin
            rec["end_balance"] = end
            rec["average_balance"] = (begin + end) / 2
            value = turnover_annualized(flow, begin, end, int(months))
            if value is None:
                rec["reason"] = "平均余额非正或流量为负，口径不成立"
            else:
                rec["value"] = value
                if begin_fallback:
                    rec["reason"] = ""  # 有值，清空错误标记
                    rec["caveat"] = "期初余额使用当年 Q1 近似（上年年报数据不可用）"
        out_rows.append(rec)
    return pd.DataFrame(out_rows)


def receivables_turnover_series(std_balance: pd.DataFrame, std_income: pd.DataFrame,
                                basis: str = "cumulative") -> pd.DataFrame:
    """应收账款周转率（年化）：分子 = 营业收入（累计或单季，取决于 basis）。"""
    return _turnover_series(std_balance, std_income, "revenue", "accounts_receivable", basis=basis)


def inventory_turnover_series(std_balance: pd.DataFrame, std_income: pd.DataFrame,
                              basis: str = "cumulative") -> pd.DataFrame:
    """存货周转率（年化）：分子 = 营业成本（累计或单季，取决于 basis）。"""
    return _turnover_series(std_balance, std_income, "operate_cost", "inventory", basis=basis)
