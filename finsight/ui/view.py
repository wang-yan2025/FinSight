"""财报展示页纯逻辑辅助（不依赖 Streamlit，可离线单元测试）。

仅做展示层格式化、组装与来源标注；取数、缓存、字段标准化、指标计算、
风险规则全部复用既有模块，不在 UI 层重复实现任何财务计算，
不改变数值口径（金额保持元；图表展示层换算亿元时显式标注）。
"""
from __future__ import annotations

import time

import pandas as pd

from finsight.config import CACHE_TTL_HOURS
from finsight.datasources import em_statements
from finsight.datasources.em_statements import KIND_CN
from finsight.datasources.symbols import to_em_symbol
from finsight.finance.field_mapping import STATEMENT_VALUE_BASIS, fields_for
from finsight.finance.indicators import (
    apply_indicator,
    current_ratio,
    debt_to_asset_ratio,
    gross_margin,
    inventory_turnover_series,
    net_margin,
    receivables_turnover_series,
    yoy_series,
)
from finsight.finance.quarterly import derive_single_quarters, period_of

MISSING_TEXT = "—"
_QUARTER_TYPE_FALLBACK = {1: "一季报", 2: "中报", 3: "三季报", 4: "年报"}

_STATUS_CN = {
    "normal": "",
    "turn_to_profit": "扭亏为盈",
    "profit_to_loss": "由盈转亏",
    "loss_narrowing": "亏损收窄",
    "loss_widening": "亏损扩大",
}


# ---------------- 基础格式化 ----------------

def format_yuan(value) -> str:
    """金额展示格式：千分位整数（元）；缺失值显示 '—'，不补造。"""
    if value is None or pd.isna(value):
        return MISSING_TEXT
    return f"{float(value):,.0f}"


def format_ratio(value) -> str:
    """比率展示：40.0%；缺失 '—'。"""
    if value is None or pd.isna(value):
        return MISSING_TEXT
    return f"{float(value):.1%}"


def format_times(value) -> str:
    """年化周转率展示：3.4 次；缺失 '—'。"""
    if value is None or pd.isna(value):
        return MISSING_TEXT
    return f"{float(value):,.1f} 次"


def format_yoy_with_status(yoy, status: str) -> str:
    """同比展示：结合状态分类，特殊状态不得显示为普通增长。"""
    if yoy is None or pd.isna(yoy):
        return MISSING_TEXT
    pct = f"{float(yoy):+.1%}"
    if status == "undefined_prior_zero":
        return "上年同期为 0，无定义"
    label = _STATUS_CN.get(status, "")
    return f"{label}（{pct}）" if label else pct


def period_label(report_date, report_type=None) -> str:
    """报告期标签，如 '2024Q2·中报'；非标准季末退化为日期字符串。"""
    if report_date is None or pd.isna(report_date):
        return MISSING_TEXT
    p = period_of(report_date)
    if p is None:
        return str(report_date)[:10]
    quarter_type = report_type if isinstance(report_type, str) and report_type else _QUARTER_TYPE_FALLBACK[p[1]]
    return f"{p[0]}Q{p[1]}·{quarter_type}"


def statement_caption(kind: str) -> str:
    """报表标题行：名称 + 数值基准（复用 field_mapping 结论）+ 单位。"""
    return f"{KIND_CN[kind]}｜数值基准：{STATEMENT_VALUE_BASIS[kind]}｜单位：元"


def build_display_frame(std: pd.DataFrame, kind: str, max_periods: int | None = None) -> pd.DataFrame:
    """标准化报表 → 展示 DataFrame（报告期倒序、中文表头、缺失 '—'）。"""
    if std.empty:
        return pd.DataFrame()
    df = std.sort_values("report_date", ascending=False)
    if max_periods is not None:
        df = df.head(max_periods)
    rows = []
    for _, r in df.iterrows():
        row = {"报告期": period_label(r["report_date"], r.get("report_type"))}
        for spec in fields_for(kind):
            row[f"{spec.standard_name}（元）"] = format_yuan(r.get(spec.code))
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------- 数据来源标注 ----------------

def fetch_with_source(code: str, kind: str, refresh: bool = False) -> tuple[pd.DataFrame, str]:
    """取数并标注来源（基于缓存文件 mtime 的间接推断）。

    文案只陈述可直接观察到的缓存文件状态，不以间接证据断言网络请求
    是否发生或是否成功（不使用"未访问接口/实时拉取成功/接口取数失败"
    等确定性措辞）：
    - 取数后出现新缓存文件 → 本地缓存（本次新写入）；
    - mtime 变化 → 本地缓存（本次已刷新）；
    - mtime 不变且未过期 → 本地缓存（未过期）；
    - mtime 不变且已过期 → 本地过期缓存（最新数据可能未获取成功）。
    """
    em_symbol = to_em_symbol(code)
    path = em_statements._cache_path(em_symbol, kind)
    mtime_before = path.stat().st_mtime if path.exists() else None

    df = em_statements.fetch_statement(code, kind, refresh=refresh)

    mtime_after = path.stat().st_mtime if path.exists() else None
    if mtime_before is None:
        source = "本地缓存（本次新写入；依据：缓存文件写入时间）"
    elif mtime_after != mtime_before:
        source = "本地缓存（本次已刷新；依据：缓存文件写入时间变化）"
    else:
        age_hours = (time.time() - mtime_after) / 3600
        if age_hours < CACHE_TTL_HOURS:
            source = f"本地缓存（{age_hours:.1f} 小时前获取，未过期）"
        else:
            source = f"本地过期缓存（{age_hours:.1f} 小时前获取；缓存未更新，最新数据可能未获取成功）"
    return df, source


# ---------------- 指标概览（全部复用指标引擎） ----------------

def _yoy_map(std: pd.DataFrame, col: str) -> dict:
    """{report_date: (yoy, status)}，累计口径。"""
    out = yoy_series(std, col, basis="cumulative")
    return {r["report_date"]: (r["yoy"], r["status"]) for _, r in out.iterrows()}


def _apply_map(std: pd.DataFrame, fn, col_map: dict) -> dict:
    """{report_date: (value, reason)}，来自 apply_indicator。"""
    out = apply_indicator(std, fn, col_map)
    return {r["report_date"]: (r["value"], r["reason"]) for _, r in out.iterrows()}


def _series_map_by_date(series_df: pd.DataFrame) -> dict:
    """{report_date: (value, reason)}，来自周转率序列。"""
    return {r["report_date"]: (r["value"], r["reason"]) for _, r in series_df.iterrows()}


def _count_reasons(maps: list[dict], names: list[str]) -> list[str]:
    """把各指标的 reason 汇总为『指标：N 期原因』清单（缺失/口径不成立说明）。"""
    notes: list[str] = []
    for name, m in zip(names, maps):
        reasons: dict[str, int] = {}
        for value, reason in m.values():
            if pd.isna(value) and reason:
                reasons[reason] = reasons.get(reason, 0) + 1
        for reason, n in sorted(reasons.items()):
            notes.append(f"{name}：{n} 期未计算——{reason}")
    return notes


INDICATOR_COLUMNS = [
    "报告期",
    "营业收入（累计，元）", "营收同比（累计）",
    "净利润（累计，元）", "净利润同比（累计）",
    "归母净利润（累计，元）", "归母同比（累计）",
    "毛利率（累计）", "净利率（累计）",
    "资产负债率（期末时点）", "流动比率（期末时点）",
    "应收账款周转率（年化，累计口径）", "存货周转率（年化，累计口径）",
]


def build_indicator_display(std_income: pd.DataFrame | None, std_balance: pd.DataFrame | None,
                            std_cashflow: pd.DataFrame | None,
                            max_rows: int | None = None) -> tuple[pd.DataFrame, list[str]]:
    """构建指标概览表（每格均为展示字符串，计算全部由指标引擎完成）。

    返回 (DataFrame, notes)：notes 为未计算期数的原因清单（不编造）。
    同比列为累计口径（与同列金额口径一致）；特殊状态（扭亏为盈等）
    使用 yoy_status 分类显示。std_cashflow 当前仅影响 notes 提示。
    """
    if std_income is None or std_income.empty:
        return pd.DataFrame(), ["利润表不可用：指标概览依赖利润表，全部指标无法计算"]

    yoy_rev, yoy_np, yoy_pnp = _yoy_map(std_income, "revenue"), _yoy_map(std_income, "net_profit"), \
        _yoy_map(std_income, "parent_net_profit")
    gm = _apply_map(std_income, gross_margin,
                    {"revenue": "revenue", "operate_cost": "operate_cost"})
    nm = _apply_map(std_income, net_margin, {"net_profit": "net_profit", "revenue": "revenue"})

    if std_balance is not None and not std_balance.empty:
        dar = _apply_map(std_balance, debt_to_asset_ratio,
                         {"total_liabilities": "total_liabilities", "total_assets": "total_assets"})
        cr = _apply_map(std_balance, current_ratio,
                        {"total_current_assets": "total_current_assets",
                         "total_current_liabilities": "total_current_liabilities"})
        ar_t = _series_map_by_date(receivables_turnover_series(std_balance, std_income))
        inv_t = _series_map_by_date(inventory_turnover_series(std_balance, std_income))
        balance_maps, balance_names = [dar, cr, ar_t, inv_t], \
            ["资产负债率", "流动比率", "应收账款周转率", "存货周转率"]
    else:
        dar = cr = ar_t = inv_t = None
        balance_maps, balance_names = [], []

    def cell(m, date, fmt):
        if m is None:
            return MISSING_TEXT
        value, _ = m.get(date, (None, ""))
        return fmt(value)

    rows = []
    for _, r in std_income.sort_values("report_date", ascending=False).iterrows():
        date = r["report_date"]
        yr, ys = yoy_rev.get(date, (None, ""))
        yn, ns = yoy_np.get(date, (None, ""))
        yp, ps = yoy_pnp.get(date, (None, ""))
        rows.append({
            "报告期": period_label(date, r.get("report_type")),
            "营业收入（累计，元）": format_yuan(r.get("revenue")),
            "营收同比（累计）": format_yoy_with_status(yr, ys),
            "净利润（累计，元）": format_yuan(r.get("net_profit")),
            "净利润同比（累计）": format_yoy_with_status(yn, ns),
            "归母净利润（累计，元）": format_yuan(r.get("parent_net_profit")),
            "归母同比（累计）": format_yoy_with_status(yp, ps),
            "毛利率（累计）": cell(gm, date, format_ratio),
            "净利率（累计）": cell(nm, date, format_ratio),
            "资产负债率（期末时点）": cell(dar, date, format_ratio),
            "流动比率（期末时点）": cell(cr, date, lambda v: MISSING_TEXT if v is None or pd.isna(v) else f"{v:,.2f}"),
            "应收账款周转率（年化，累计口径）": cell(ar_t, date, format_times),
            "存货周转率（年化，累计口径）": cell(inv_t, date, format_times),
        })
    df = pd.DataFrame(rows, columns=INDICATOR_COLUMNS)
    if max_rows is not None:
        df = df.head(max_rows)

    notes = _count_reasons(
        [yoy_rev, yoy_np, yoy_pnp, gm, nm] + balance_maps,
        ["营收同比", "净利润同比", "归母同比", "毛利率", "净利率"] + balance_names,
    )
    if std_balance is None or std_balance.empty:
        notes.append("资产负债表不可用：偿债能力与周转率类指标整列无法计算")
    if std_cashflow is None or std_cashflow.empty:
        notes.append("现金流量表不可用：经营现金流相关趋势与 R3 规则无法评估（见风险信号页）")
    return df, notes


# ---------------- 趋势帧（图表数据，缺失不插值） ----------------

def build_trend_frames(std_income, std_cashflow, std_balance) -> dict[str, pd.DataFrame]:
    """构建图表用 DataFrame：{'单季流量', '盈利比率', '偿债比率'}。

    - 单季流量（亿元）：单季营业收入/净利润/经营现金流净额（单季口径，
      经 derive_single_quarters 还原；缺失期为 NaN，不插值）；
    - 盈利比率（%）：毛利率、净利率（累计口径，%值）；
    - 偿债比率：资产负债率（%）、流动比率（倍）（期末时点值）。
    各图表内部口径一致，不混用单季与累计。
    """
    frames: dict[str, pd.DataFrame] = {}

    def single_quarter_series(std, col) -> pd.Series | None:
        """按 report_date 索引的单季值序列；不可得返回 None。"""
        if std is None or std.empty or col not in std.columns:
            return None
        derived = derive_single_quarters(std, [col]).frame
        s = pd.to_numeric(derived[f"{col}__sq"], errors="coerce")
        s.index = derived["report_date"].to_numpy()
        return s

    flow_parts: list[pd.Series] = []
    if std_income is not None and not std_income.empty:
        for col, name in (("revenue", "营业收入（单季）"), ("net_profit", "净利润（单季）")):
            s = single_quarter_series(std_income, col)
            if s is not None:
                flow_parts.append(s.rename(name))
    if std_cashflow is not None and not std_cashflow.empty:
        s = single_quarter_series(std_cashflow, "operating_cash_flow")
        if s is not None:
            flow_parts.append(s.rename("经营现金流净额（单季）"))
    if flow_parts:
        frames["单季流量"] = (pd.concat(flow_parts, axis=1, join="outer")
                              .sort_index() / 1e8)  # 元 → 亿元（仅展示单位换算）

    if std_income is not None and not std_income.empty:
        gm = _apply_map(std_income, gross_margin,
                        {"revenue": "revenue", "operate_cost": "operate_cost"})
        nm = _apply_map(std_income, net_margin,
                        {"net_profit": "net_profit", "revenue": "revenue"})
        idx = std_income["report_date"]
        frames["盈利比率"] = pd.DataFrame({
            "毛利率（%，累计口径）": [gm.get(d, (None,))[0] for d in idx],
            "净利率（%，累计口径）": [nm.get(d, (None,))[0] for d in idx],
        }, index=idx) * 100
        frames["盈利比率"] = frames["盈利比率"].sort_index()

    if std_balance is not None and not std_balance.empty:
        dar = _apply_map(std_balance, debt_to_asset_ratio,
                         {"total_liabilities": "total_liabilities", "total_assets": "total_assets"})
        cr = _apply_map(std_balance, current_ratio,
                        {"total_current_assets": "total_current_assets",
                         "total_current_liabilities": "total_current_liabilities"})
        idx = std_balance["report_date"]
        frame = pd.DataFrame({
            "资产负债率（%，期末时点）": [dar.get(d, (None,))[0] for d in idx],
            "流动比率（倍，期末时点）": [cr.get(d, (None,))[0] for d in idx],
        }, index=idx)
        frame["资产负债率（%，期末时点）"] = frame["资产负债率（%，期末时点）"] * 100
        frames["偿债比率"] = frame.sort_index()
    return frames
