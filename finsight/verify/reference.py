"""核验参考数据：从标准化三表独立重算的参考快照。

独立性原则：
- 只使用 standardize_statement 的输出与 finance 引擎（indicators/
  quarterly）重算，绝不读取被核验报告（含备忘录）的证据表或结论。
- ReferenceSnapshot 记录快照指纹（关键值摘要），用于识别报告生成后
  参考数据是否发生变化（财报修订/缓存更新）。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field as dc_field

import pandas as pd

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
from finsight.ui.signals_view import evaluate_available

# 指标注册表：claim 指标 key → (类别, 标准字段/计算, 单位, 口径族)
# 类别：flow（期间流量，有累计/单季两口径） / point（时点余额） /
#       ratio（比率，按累计期计算） / yoy（同比，附状态）
METRIC_REGISTRY = {
    "revenue":            ("flow", "revenue", "元"),
    "net_profit":         ("flow", "net_profit", "元"),
    "parent_net_profit":  ("flow", "parent_net_profit", "元"),
    "operating_cash_flow": ("flow", "operating_cash_flow", "元"),
    "total_assets":       ("point", "total_assets", "元"),
    "total_liabilities":  ("point", "total_liabilities", "元"),
    "accounts_receivable": ("point", "accounts_receivable", "元"),
    "inventory":          ("point", "inventory", "元"),
    "gross_margin":       ("ratio", None, "%"),
    "net_margin":         ("ratio", None, "%"),
    "debt_to_asset_ratio": ("ratio", None, "%"),
    "current_ratio":      ("ratio", None, "倍"),
    "receivables_turnover": ("ratio", None, "次"),
    "inventory_turnover": ("ratio", None, "次"),
    "ocf_np_ratio":       ("ratio", None, "倍"),
    "revenue_yoy":        ("yoy", "revenue", "%"),
    "net_profit_yoy":     ("yoy", "net_profit", "%"),
    "parent_net_profit_yoy": ("yoy", "parent_net_profit", "%"),
}


def _period_tag(date) -> str:
    p = period_of(date)
    return f"{p[0]}Q{p[1]}" if p else str(date)[:10]


@dataclass
class ReferenceSnapshot:
    """独立参考快照：按（指标, 期间, 口径）取值。"""

    code: str
    target_period: str
    periods: dict                       # 各表实际采用期间
    sources: dict
    cumulative: dict = dc_field(default_factory=dict)   # {(metric, tag): value}
    single_quarter: dict = dc_field(default_factory=dict)
    yoy: dict = dc_field(default_factory=dict)          # {(metric, tag): (yoy, status)} 累计口径
    yoy_single: dict = dc_field(default_factory=dict)   # {(metric, tag): (yoy, status)} 单季口径
    signals: list = dc_field(default_factory=list)
    fingerprint: str = ""
    coverage: tuple = ("", "")                          # (最早期间, 最晚期间)

    def value(self, metric: str, tag: str, basis: str):
        """按口径取参考值。basis: cumulative / single_quarter。"""
        kind = METRIC_REGISTRY[metric][0]
        if kind == "point":
            return self.cumulative.get((metric, tag))
        if basis == "single_quarter":
            return self.single_quarter.get((metric, tag))
        return self.cumulative.get((metric, tag))

    def available_periods(self, metric: str, basis: str = "cumulative") -> set[str]:
        kind = METRIC_REGISTRY[metric][0]
        if kind == "point":
            return {tag for m, tag in self.cumulative if m == metric}
        table = self.single_quarter if basis == "single_quarter" else self.cumulative
        return {tag for m, tag in table if m == metric}

    def fingerprint_values(self) -> dict:
        """参与指纹的关键值（用于新旧快照差异检测）。"""
        return {f"{m}@{t}": v for (m, t), v in sorted(self.cumulative.items())}


def build_reference_snapshot(code: str, std_income, std_balance, std_cashflow,
                             sources: dict | None = None) -> ReferenceSnapshot:
    """从标准化三表独立重算参考值（不依赖任何报告内容）。"""
    snap = ReferenceSnapshot(code=code, target_period="", periods={}, sources=dict(sources or {}))
    if std_income is None or std_income.empty:
        return snap

    # ---- 流量字段：累计与单季两套 ----
    flow_fields = [("revenue", "revenue"), ("net_profit", "net_profit"),
                   ("parent_net_profit", "parent_net_profit")]
    for metric, col in flow_fields:
        if col not in std_income.columns:
            continue
        for _, r in std_income.iterrows():
            tag = _period_tag(r["report_date"])
            if pd.notna(r[col]):
                snap.cumulative[(metric, tag)] = float(r[col])
        derived = derive_single_quarters(std_income, [col]).frame
        for _, r in derived.iterrows():
            if pd.notna(r["year"]) and pd.notna(r[f"{col}__sq"]):
                snap.single_quarter[(metric, f"{int(r['year'])}Q{int(r['quarter'])}")] = \
                    float(r[f"{col}__sq"])
    if std_cashflow is not None and not std_cashflow.empty:
        for _, r in std_cashflow.iterrows():
            tag = _period_tag(r["report_date"])
            if pd.notna(r.get("operating_cash_flow")):
                snap.cumulative[("operating_cash_flow", tag)] = float(r["operating_cash_flow"])
        derived = derive_single_quarters(std_cashflow, ["operating_cash_flow"]).frame
        for _, r in derived.iterrows():
            if pd.notna(r["year"]) and pd.notna(r["operating_cash_flow__sq"]):
                snap.single_quarter[("operating_cash_flow", f"{int(r['year'])}Q{int(r['quarter'])}")] = \
                    float(r["operating_cash_flow__sq"])

    latest = std_income["report_date"].max()
    snap.target_period = _period_tag(latest)
    snap.periods = {"income": snap.target_period,
                    "balance": _period_tag(std_balance["report_date"].max())
                    if std_balance is not None and not std_balance.empty else None,
                    "cashflow": _period_tag(std_cashflow["report_date"].max())
                    if std_cashflow is not None and not std_cashflow.empty else None}

    # ---- 时点余额（资产负债表）----
    if std_balance is not None and not std_balance.empty:
        for metric in ("total_assets", "total_liabilities", "accounts_receivable", "inventory"):
            if metric in std_balance.columns:
                for _, r in std_balance.iterrows():
                    if pd.notna(r[metric]):
                        snap.cumulative[(metric, _period_tag(r["report_date"]))] = float(r[metric])

    # ---- 比率（复用指标引擎重算）----
    def _apply_map(df, fn, col_map):
        out = apply_indicator(df, fn, col_map)
        return {_period_tag(r["report_date"]): float(r["value"])
                for _, r in out.iterrows() if pd.notna(r["value"])}

    if "revenue" in std_income.columns and "operate_cost" in std_income.columns:
        snap.cumulative.update({("gross_margin", k): v for k, v in _apply_map(
            std_income, gross_margin,
            {"revenue": "revenue", "operate_cost": "operate_cost"}).items()})
    snap.cumulative.update({("net_margin", k): v for k, v in _apply_map(
        std_income, net_margin, {"net_profit": "net_profit", "revenue": "revenue"}).items()})
    if std_balance is not None and not std_balance.empty:
        snap.cumulative.update({("debt_to_asset_ratio", k): v for k, v in _apply_map(
            std_balance, debt_to_asset_ratio,
            {"total_liabilities": "total_liabilities", "total_assets": "total_assets"}).items()})
        snap.cumulative.update({("current_ratio", k): v for k, v in _apply_map(
            std_balance, current_ratio,
            {"total_current_assets": "total_current_assets",
             "total_current_liabilities": "total_current_liabilities"}).items()})
        for prefix, series in (("receivables_turnover", receivables_turnover_series(std_balance, std_income)),
                               ("inventory_turnover", inventory_turnover_series(std_balance, std_income))):
            for _, r in series.iterrows():
                if pd.notna(r["value"]):
                    snap.cumulative[(prefix, _period_tag(r["report_date"]))] = float(r["value"])

    # ---- 同比（含状态，累计与单季两口径）----
    for metric, col in (("revenue_yoy", "revenue"), ("net_profit_yoy", "net_profit"),
                        ("parent_net_profit_yoy", "parent_net_profit")):
        if col not in std_income.columns:
            continue
        for basis, table in (("cumulative", snap.yoy), ("single_quarter", snap.yoy_single)):
            out = yoy_series(std_income, col, basis=basis)
            for _, r in out.iterrows():
                if pd.notna(r["yoy"]):
                    table[(metric, _period_tag(r["report_date"]))] = (float(r["yoy"]), r["status"])

    # ---- 经营现金流/净利润（利润质量比率，分母为正时）----
    if std_cashflow is not None and not std_cashflow.empty:
        for _, r in std_income.iterrows():
            tag = _period_tag(r["report_date"])
            ocf = snap.cumulative.get(("operating_cash_flow", tag))
            np_v = r.get("net_profit")
            if ocf is not None and pd.notna(np_v) and float(np_v) > 0:
                snap.cumulative[("ocf_np_ratio", tag)] = ocf / float(np_v)

    # ---- 风险信号（供定性声明参照，不作通过依据）----
    analysis = evaluate_available(std_income, std_cashflow, std_balance, company=code)
    snap.signals = analysis.signals

    tags = {tag for _, tag in snap.cumulative}
    ordered = sorted(tags)
    snap.coverage = (ordered[0] if ordered else "", ordered[-1] if ordered else "")

    digest = hashlib.sha256(
        ";".join(f"{k}={v:.6f}" for k, v in snap.fingerprint_values().items()).encode("utf-8")
    ).hexdigest()[:16]
    snap.fingerprint = digest
    return snap
