"""财务风险信号识别引擎（阶段3 MVP；纯规则，无 UI、无 LLM）。

定位声明
========
- 本引擎输出的所有 Signal 均为 **风险提示（risk_hint）**，不是异常结论：
  单一或多个信号均不能据此认定财务造假或违规，必须经人工核查。
- 阈值为第一版可配置启发式参数（DEFAULT_THRESHOLDS），不代表行业统一
  标准；调用方可传入 thresholds 覆盖。
- 数据不足时返回明确原因（RuleResult.unevaluated），不编造数值或证据。

四类规则（触发条件）
====================
R1 收入增速放缓（level=medium）——触发条件（满足其一，优先单季口径，禁止混比）：
  a) 同比增速较上一可比期间下降 ≥ r1_pp_drop（默认 10 个百分点，含 1e-9
     绝对容差的浮点边界保护）；上一可比期间 = 同年上一季度（Q1 的可比期间
     为上年 Q4），同口径双侧可得才比较；正/负增长区间均适用：持续负增长
     且恶化达阈值时亦按本条触发（caveat 注明属持续负增长）。
  b) 由非负增长转为负增长：上一可比期间同比 ≥ 0 且当期同比 < 0
     （上期恰为零时 caveat 注明"非负转负"，不称"为正增长"）。
  不触发（属"已评估未触发"，不记入 unevaluated，与触发条件不混用）：
  - 持续负增长但改善（上期 < 0、当期降幅收窄）；
  - 持续负增长且恶化不足阈值（含同比持平）。
  unevaluated 仅记录真正无法评估的期间：当期同比不可得，或同口径下
  上一可比期间不可得（无法判定是否由正转负，也无法计算增速变动）。
  「收入仍处于负增长」如需提示，应作为独立信号规则（与 R1 区分），
  当前版本未启用。
  口径规则：优先单季同比；单季不可得（单季还原失败）时回退累计同比，
  并在 metrics["口径"] 与 caveats 中显式标注，绝不混比两种口径。

R2 收入增长、利润下滑（level=medium）
  同一可比期间、同一口径下：营业收入同比 > 0，且 净利润同比 < 0 或
  归母净利润同比 < 0（两口径分别计算、分别标注，不混用）。
  三个指标的口径必须一致（同为单季或同为累计），口径由三者共同可得性
  决定：优先单季，回退累计并标注。

R3 经营现金流与净利润背离
  同一可比期间、同一口径（优先单季，回退累计并标注）：
  a) 净利润 > 0 且 经营现金流净额 < 0 → level=high（较高关注）；
  b) 净利润 > 0 且 经营现金流净额/净利润 < r3_low_ratio（默认 0.5）
     → level=medium。比率仅在分母（净利润）为正且分子分母数据有效时
     计算；净利润 ≤ 0 的期间不评估本规则（前提不满足，记入 unevaluated）。

R4 应收账款增长过快（level=medium）
  应收账款期末余额同比增速 − 同期营业收入同比增速 ≥ r4_gap_pp
  （默认 20 个百分点）。余额同比 = 时点对时点（(y,q) vs (y-1,q)），
  报告期严格对齐；营收同比取累计口径作期间对照（口径在 fields_used
  标注）。上年同期余额为 0 → 同比无定义，记入 unevaluated。

单位与来源
==========
- 所有金额单位：元（继承 field_mapping 实测结论）。
- 数据来源：东方财富三大报表（经 akshare 拉取，本地缓存），字段与
  口径见 fields_used；未经证实的数据一律不引用。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field as dc_field

import pandas as pd

from finsight.finance.indicators import yoy_growth, yoy_series, yoy_status
from finsight.finance.quarterly import derive_single_quarters, period_of

DEFAULT_THRESHOLDS = {
    "r1_pp_drop": 0.10,    # R1：增速下降阈值（百分点/100）
    "r3_low_ratio": 0.5,   # R3：经营现金流/净利润 低比率阈值
    "r4_gap_pp": 0.20,     # R4：应收增速超出营收增速阈值（百分点/100）
}

DATA_SOURCE = "东方财富三大报表（经 akshare，本地缓存快照）"

_REVENUE_FIELD = ("营业收入", "利润表 OPERATE_INCOME（东财，元），累计值；单季口径经 derive_single_quarters 还原")
_NETPROFIT_FIELD = ("净利润", "利润表 NETPROFIT（东财，元，含少数股东损益），累计值；单季口径经还原")
_PNP_FIELD = ("归母净利润", "利润表 PARENT_NETPROFIT（东财，元，归母口径），累计值；单季口径经还原")
_OCF_FIELD = ("经营活动现金流净额", "现金流量表 NETCASH_OPERATE（东财，元），累计值；单季口径经还原")
_AR_FIELD = ("应收账款", "资产负债表 ACCOUNTS_RECE（东财，元），报告期末时点值")


@dataclass(frozen=True)
class Signal:
    """一条风险提示信号（kind 恒为 risk_hint，非异常结论）。"""

    rule_id: str                 # R1-R4
    name: str                    # 信号名称
    level: str                   # high / medium
    kind: str                    # 恒为 "risk_hint"
    company: str                 # 公司标识（调用方传入，如 EM 代码）
    period: str                  # 如 "2024Q2"
    report_date: pd.Timestamp
    condition: str               # 触发条件与判定描述
    metrics: dict                # 实际数值（含口径标注）
    fields_used: dict            # 涉及字段 -> 口径与数据来源
    caveats: list[str] = dc_field(default_factory=list)
    interpretation: str = ""     # 风险解释（提示性措辞）
    suggestions: list[str] = dc_field(default_factory=list)  # 建议人工核查事项


@dataclass
class RuleResult:
    """单条规则的评估结果：触发信号 + 未评估期间的明确原因。"""

    rule_id: str
    signals: list[Signal] = dc_field(default_factory=list)
    unevaluated: list[str] = dc_field(default_factory=list)


@dataclass
class AnalysisResult:
    company: str
    signals: list[Signal] = dc_field(default_factory=list)
    unevaluated: list[str] = dc_field(default_factory=list)


# ---------------- 内部工具：口径对齐的取数与同比查表 ----------------

def _series_map(series_out: pd.DataFrame) -> dict[tuple[int, int], dict]:
    """把 yoy_series 输出转为 {(年, 季): {yoy, status, reason, basis}}。"""
    m: dict[tuple[int, int], dict] = {}
    for _, r in series_out.iterrows():
        if pd.notna(r["year"]):
            m[(int(r["year"]), int(r["quarter"]))] = {
                "yoy": float(r["yoy"]) if pd.notna(r["yoy"]) else None,
                "status": r["status"],
                "reason": r["reason"],
                "basis": r["basis"],
            }
    return m


def _yoy_maps(std: pd.DataFrame, col: str) -> tuple[dict, dict]:
    """返回（单季同比表, 累计同比表）。"""
    return (_series_map(yoy_series(std, col, "single_quarter")),
            _series_map(yoy_series(std, col, "cumulative")))


def _pick(primary: dict, fallback: dict, period: tuple[int, int]):
    """按优先级取某期同比：primary 优先，fallback 回退；均无返回 (None, None)。"""
    entry = primary.get(period)
    if entry and entry["yoy"] is not None:
        return entry, entry["basis"]
    entry = fallback.get(period)
    if entry and entry["yoy"] is not None:
        return entry, entry["basis"]
    return None, None


def _value_map(std: pd.DataFrame, col: str, basis: str) -> dict[tuple[int, int], float]:
    """{(年, 季): 数值}。basis="cumulative" 直接取值；"single_quarter" 经还原。"""
    m: dict[tuple[int, int], float] = {}
    if basis == "single_quarter":
        frame = derive_single_quarters(std, [col]).frame
        src = f"{col}__sq"
        for _, r in frame.iterrows():
            if pd.notna(r["year"]) and pd.notna(r[src]):
                m[(int(r["year"]), int(r["quarter"]))] = float(r[src])
    else:
        for _, r in std.iterrows():
            p = period_of(r["report_date"])
            if p is not None and pd.notna(r[col]):
                m[p] = float(r[col])
    return m


def _fmt_pct(x: float | None) -> str:
    return "缺失" if x is None else f"{x:+.1%}"


# ---------------- R1 收入增速放缓 ----------------

def rule1_revenue_slowdown(std_income: pd.DataFrame, company: str = "",
                           thresholds: dict | None = None) -> RuleResult:
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    single, cumulative = _yoy_maps(std_income, "revenue")
    result = RuleResult(rule_id="R1")
    for period in sorted(set(single) | set(cumulative)):
        year, quarter = period
        prev_key = (year, quarter - 1) if quarter > 1 else (year - 1, 4)
        # 口径纪律：当期与上一可比期间必须在同一口径下【双侧可得】才比较，
        # 优先单季、回退累计（标注）；绝不单季对累计跨口径混比。
        cur = prev = None
        cur_basis = None
        for basis_name, table in (("single_quarter", single), ("cumulative", cumulative)):
            c = table.get(period)
            p = table.get(prev_key)
            if c and c["yoy"] is not None and p is not None and p["yoy"] is not None:
                cur, prev, cur_basis = c, p, basis_name
                break
        if cur is None:
            fallback_cur, _ = _pick(single, cumulative, period)
            if fallback_cur is None:
                result.unevaluated.append(
                    f"R1 {year}Q{quarter}: 营业收入同比不可得（单季与累计口径均缺失），无法评估")
            else:
                result.unevaluated.append(
                    f"R1 {year}Q{quarter}: 上一可比期间（{prev_key[0]}Q{prev_key[1]}）同比在同口径下不可得，"
                    f"无法判定是否由正转负，也无法计算增速变动；"
                    f"当期同比 {_fmt_pct(fallback_cur['yoy'])}，未触发")
            continue

        caveats: list[str] = []
        if cur_basis == "cumulative":
            caveats.append("单季同比不可得（单季还原失败），已回退累计口径评估，口径见 metrics['口径']")

        negative = cur["yoy"] < 0
        turned_negative = negative and prev["yoy"] >= 0  # 由非负转负（prev 同口径已确认）
        # 降幅判定：≥ 阈值，或与阈值之差在严格绝对容差内（浮点边界保护，
        # 如 +20% → +10% 的理论恰降 10pp 因二进制表示可能差 1e-16 而漏判）
        drop_amount = prev["yoy"] - cur["yoy"]
        drop = drop_amount >= th["r1_pp_drop"] or math.isclose(
            drop_amount, th["r1_pp_drop"], rel_tol=0.0, abs_tol=1e-9)
        # 已完成同口径比较但不满足触发条件的期间（含持续负增长改善/恶化不足阈值）
        # 属"已评估未触发"，不记入 unevaluated。
        if not (turned_negative or drop):
            continue

        reasons = []
        if drop:
            reasons.append(f"同比增速较上一可比期间下降 {drop_amount * 100:.1f} 个百分点"
                           f"（{_fmt_pct(prev['yoy'])} → {_fmt_pct(cur['yoy'])}）")
        if turned_negative:
            reasons.append(f"同比转为负增长（{_fmt_pct(cur['yoy'])}）")
            if prev["yoy"] > 0:
                caveats.append("上一可比期间为正增长，本期转负")
            else:
                caveats.append("上一可比期间同比恰为零（非负），本期转负")
        if drop and not turned_negative and prev["yoy"] < 0:
            caveats.append("上一可比期间同比亦为负，属持续负增长；本条按降幅条件触发")
        report_date = _report_date_of(std_income, period)
        result.signals.append(Signal(
            rule_id="R1", name="收入增速放缓", level="medium", kind="risk_hint",
            company=company, period=f"{year}Q{quarter}", report_date=report_date,
            condition="；".join(reasons),
            metrics={
                "当期营业收入同比": cur["yoy"],
                "上一可比期间同比": prev["yoy"],
                "增速变动(百分点)": (prev["yoy"] - cur["yoy"]) * 100,
                "口径": "单季" if cur_basis == "single_quarter" else "累计（回退）",
            },
            fields_used={"营业收入": _REVENUE_FIELD[1], "数据来源": DATA_SOURCE},
            caveats=caveats,
            interpretation=(
                "营业收入同比增速明显放缓或转负，可能反映下游需求走弱、公司收入确认节奏变化"
                "或高基数效应；此为风险提示，需结合业务与同业情况人工核查，不构成异常结论。"),
            suggestions=[
                "核查公司对收入下滑的解释（定期报告经营讨论与分析）",
                "对比同行业可比公司同期收入增速",
                "核查收入确认政策与会计估计是否变更（附注）",
            ],
        ))
    return result


def _report_date_of(std: pd.DataFrame, period: tuple[int, int]) -> pd.Timestamp:
    for _, r in std.iterrows():
        if period_of(r["report_date"]) == period:
            return r["report_date"]
    return pd.NaT


# ---------------- R2 收入增长、利润下滑 ----------------

def rule2_revenue_up_profit_down(std_income: pd.DataFrame, company: str = "",
                                 thresholds: dict | None = None) -> RuleResult:
    result = RuleResult(rule_id="R2")
    maps = {
        "营业收入": _yoy_maps(std_income, "revenue"),
        "净利润": _yoy_maps(std_income, "net_profit"),
        "归母净利润": _yoy_maps(std_income, "parent_net_profit"),
    }
    all_periods = set()
    for s, c in maps.values():
        all_periods |= set(s) | set(c)

    for period in sorted(all_periods):
        year, quarter = period
        # 三指标口径强制一致：先试单季（三者齐备），再试累计（三者齐备）
        picked, basis = None, None
        for b, idx in (("single_quarter", 0), ("cumulative", 1)):
            entries = {name: m[idx].get(period) for name, m in maps.items()}
            if all(e and e["yoy"] is not None for e in entries.values()):
                picked, basis = entries, b
                break
        if picked is None:
            result.unevaluated.append(
                f"R2 {year}Q{quarter}: 营业收入/净利润/归母净利润同比在同一口径下不齐备，无法评估")
            continue

        rev_yoy = picked["营业收入"]["yoy"]
        if rev_yoy <= 0:
            continue  # 规则前提：收入正增长
        decliners = [n for n in ("净利润", "归母净利润") if picked[n]["yoy"] < 0]
        if not decliners:
            continue

        caveats = ["单季口径不齐备，已回退累计口径（三指标同口径）"
                   if basis == "cumulative" else ""]
        caveats = [c for c in caveats if c]
        result.signals.append(Signal(
            rule_id="R2", name="收入增长、利润下滑", level="medium", kind="risk_hint",
            company=company, period=f"{year}Q{quarter}",
            report_date=_report_date_of(std_income, period),
            condition=(f"营业收入同比 {_fmt_pct(rev_yoy)} 为正，而"
                       + "、".join(decliners)
                       + "同比下滑（" + "、".join(_fmt_pct(picked[n]["yoy"]) for n in decliners) + "）"),
            metrics={
                "营业收入同比": rev_yoy,
                "净利润同比": picked["净利润"]["yoy"],
                "净利润同比状态": picked["净利润"]["status"],
                "归母净利润同比": picked["归母净利润"]["yoy"],
                "归母净利润同比状态": picked["归母净利润"]["status"],
                "口径": "单季" if basis == "single_quarter" else "累计（回退）",
            },
            fields_used={"营业收入": _REVENUE_FIELD[1],
                         "净利润": _NETPROFIT_FIELD[1],
                         "归母净利润": _PNP_FIELD[1],
                         "数据来源": DATA_SOURCE},
            caveats=caveats,
            interpretation=(
                "收入增长而利润下滑，可能源于毛利率承压、费用或减值计提上升；净利润与归母"
                "净利润两口径差异提示少数股东损益结构变化。此为风险提示，需人工核查，"
                "不构成异常结论。"),
            suggestions=[
                "核查毛利率变化与成本结构（营业成本、期间费用明细）",
                "核查资产减值/信用减值损失计提情况",
                "对比净利润与归母净利润口径差异（少数股东损益、子公司结构）",
            ],
        ))
    return result


# ---------------- R3 经营现金流与净利润背离 ----------------

def rule3_cashflow_profit_divergence(std_income: pd.DataFrame, std_cashflow: pd.DataFrame,
                                     company: str = "", thresholds: dict | None = None) -> RuleResult:
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    result = RuleResult(rule_id="R3")
    np_sq, ocf_sq = _value_map(std_income, "net_profit", "single_quarter"), \
        _value_map(std_cashflow, "operating_cash_flow", "single_quarter")
    np_cum, ocf_cum = _value_map(std_income, "net_profit", "cumulative"), \
        _value_map(std_cashflow, "operating_cash_flow", "cumulative")
    periods = sorted(set(np_sq) | set(ocf_sq) | set(np_cum) | set(ocf_cum))

    for period in periods:
        year, quarter = period
        np_v = ocf_v = None
        basis = None
        for b, (npm, ocfm) in (("single_quarter", (np_sq, ocf_sq)),
                               ("cumulative", (np_cum, ocf_cum))):
            if period in npm and period in ocfm:
                np_v, ocf_v, basis = npm[period], ocfm[period], b
                break
        if np_v is None:
            result.unevaluated.append(
                f"R3 {year}Q{quarter}: 净利润与经营现金流在同一口径下不齐备，无法评估")
            continue
        if np_v <= 0:
            result.unevaluated.append(
                f"R3 {year}Q{quarter}: 净利润为 {np_v:.3e} 元（非正），规则前提（净利润为正）不满足，不评估")
            continue

        ratio = ocf_v / np_v
        high = ocf_v < 0
        if not high and ratio >= th["r3_low_ratio"]:
            continue
        caveats = (["单季口径不齐备，已回退累计口径（净利润与现金流同口径比较）"]
                   if basis == "cumulative" else [])
        report_date = _report_date_of(std_cashflow, period)
        if pd.isna(report_date):
            report_date = _report_date_of(std_income, period)
        result.signals.append(Signal(
            rule_id="R3",
            name="经营现金流与净利润背离",
            level="high" if high else "medium",
            kind="risk_hint",
            company=company, period=f"{year}Q{quarter}", report_date=report_date,
            condition=(f"净利润为正（{np_v:.3e} 元）而经营活动现金流净额为负（{ocf_v:.3e} 元）"
                       if high else
                       f"经营现金流净额/净利润 = {ratio:.2f}，低于阈值 {th['r3_low_ratio']}"),
            metrics={
                "净利润(元)": np_v,
                "经营活动现金流净额(元)": ocf_v,
                "经营现金流/净利润": ratio,
                "口径": "单季" if basis == "single_quarter" else "累计（回退）",
            },
            fields_used={"净利润": _NETPROFIT_FIELD[1],
                         "经营活动现金流净额": _OCF_FIELD[1],
                         "数据来源": DATA_SOURCE},
            caveats=caveats,
            interpretation=(
                "账面利润与经营现金流入背离，可能反映应收/存货对营运资金的占用增加、收入确认"
                "与收款节奏错位或利润质量下降。此为风险提示，需人工核查，不构成异常结论。"),
            suggestions=[
                "核对现金流量表补充资料：净利润调节为经营活动现金流的过程",
                "核查应收账款、存货、合同负债等营运资金项目的变动",
                "核查收款政策、票据结算方式是否发生变化",
            ],
        ))
    return result


# ---------------- R4 应收账款增长过快 ----------------

def rule4_receivables_growth(std_income: pd.DataFrame, std_balance: pd.DataFrame,
                             company: str = "", thresholds: dict | None = None) -> RuleResult:
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    result = RuleResult(rule_id="R4")
    ar: dict[tuple[int, int], float] = {}
    for _, r in std_balance.iterrows():
        p = period_of(r["report_date"])
        if p is not None and pd.notna(r["accounts_receivable"]):
            ar[p] = float(r["accounts_receivable"])

    rev_single, rev_cum = _yoy_maps(std_income, "revenue")
    for period in sorted(ar):
        year, quarter = period
        cur_ar = ar[period]
        prior_ar = ar.get((year - 1, quarter))
        if prior_ar is None:
            result.unevaluated.append(
                f"R4 {year}Q{quarter}: 上年同期（{year - 1}Q{quarter}）应收账款余额缺失，无法计算同比")
            continue
        if prior_ar == 0:
            result.unevaluated.append(
                f"R4 {year}Q{quarter}: 上年同期应收账款余额为 0，同比无定义")
            continue
        ar_yoy = yoy_growth(cur_ar, prior_ar)
        ar_status = yoy_status(cur_ar, prior_ar)
        # 营收同比取累计口径作期间对照（余额为时点同比，两者均同期对齐）
        rev, rev_basis = _pick(rev_cum, rev_single, period)
        if rev is None:
            result.unevaluated.append(
                f"R4 {year}Q{quarter}: 同期营业收入同比不可得（单季与累计口径均缺失），无法比较")
            continue
        gap = ar_yoy - rev["yoy"]
        if gap < th["r4_gap_pp"]:
            continue
        result.signals.append(Signal(
            rule_id="R4", name="应收账款增长过快", level="medium", kind="risk_hint",
            company=company, period=f"{year}Q{quarter}",
            report_date=_report_date_of(std_balance, period),
            condition=(f"应收账款同比 {_fmt_pct(ar_yoy)} 高于营业收入同比 {_fmt_pct(rev['yoy'])} "
                       f"达 {gap * 100:.1f} 个百分点（阈值 {th['r4_gap_pp'] * 100:.0f}）"),
            metrics={
                "应收账款期末余额(元)": cur_ar,
                "应收账款同比": ar_yoy,
                "应收账款同比状态": ar_status,
                "营业收入同比（累计口径）": rev["yoy"],
                "增速差(百分点)": gap * 100,
            },
            fields_used={"应收账款": _AR_FIELD[1],
                         "营业收入": _REVENUE_FIELD[1] + "；本规则取累计口径同比作期间对照",
                         "数据来源": DATA_SOURCE},
            caveats=["应收账款为时点余额同比，营业收入为累计口径同比，两者均按报告期严格对齐；"
                     "余额基数较小的公司（如先款后货模式）同比波动可能被放大，解读需谨慎"
                     if cur_ar < prior_ar * 3 and prior_ar < 1e8 else
                     "应收账款为时点余额同比，营业收入为累计口径同比，两者均按报告期严格对齐"],
            interpretation=(
                "应收账款增速显著快于收入增速，可能指向信用政策放宽、回款质量下降或收入确认"
                "激进的迹象之一。此为风险提示，需人工核查，不构成异常结论。"),
            suggestions=[
                "核查应收账款账龄结构与坏账准备计提充分性（附注）",
                "核查信用政策与主要客户结算条款是否变化",
                "对照合同负债/预收款项与收入确认政策",
            ],
        ))
    return result


# ---------------- 汇总入口 ----------------

def evaluate_all(std_income: pd.DataFrame, std_cashflow: pd.DataFrame,
                 std_balance: pd.DataFrame, company: str = "",
                 thresholds: dict | None = None) -> AnalysisResult:
    """对一份标准化三表数据运行全部规则，汇总信号与未评估原因。"""
    results = [
        rule1_revenue_slowdown(std_income, company, thresholds),
        rule2_revenue_up_profit_down(std_income, company, thresholds),
        rule3_cashflow_profit_divergence(std_income, std_cashflow, company, thresholds),
        rule4_receivables_growth(std_income, std_balance, company, thresholds),
    ]
    signals = [s for r in results for s in r.signals]
    unevaluated = [n for r in results for n in r.unevaluated]
    return AnalysisResult(company=company, signals=signals, unevaluated=unevaluated)
