"""风险信号展示层编排与格式化（不修改 R1—R4 规则定义与阈值）。

- 三表齐备时复用 evaluate_all；部分报表不可用时逐规则降级：
  输入缺失的规则记入 unevaluated（"因数据不足无法评估"），
  与"已评估但未触发"严格区分。
- 所有信号均为 risk_hint（风险提示），不得表述为财务造假或违规结论。
"""
from __future__ import annotations

import pandas as pd

from finsight.finance.signals import (
    AnalysisResult,
    evaluate_all,
    rule1_revenue_slowdown,
    rule2_revenue_up_profit_down,
    rule3_cashflow_profit_divergence,
    rule4_receivables_growth,
)

LEVEL_CN = {"high": "较高关注", "medium": "关注"}

RULE_NAMES = {
    "R1": "收入增速放缓",
    "R2": "收入增长、利润下滑",
    "R3": "经营现金流与利润背离",
    "R4": "应收账款增长过快",
}


def evaluate_available(std_income: pd.DataFrame | None, std_cashflow: pd.DataFrame | None,
                       std_balance: pd.DataFrame | None, company: str = "") -> AnalysisResult:
    """按可用报表评估规则：三表齐备走 evaluate_all，否则逐规则降级。"""
    if std_income is not None and std_cashflow is not None and std_balance is not None:
        return evaluate_all(std_income, std_cashflow, std_balance, company=company)

    signals, unevaluated = [], []
    if std_income is None:
        for rid in ("R1", "R2", "R3", "R4"):
            unevaluated.append(f"{rid} {RULE_NAMES[rid]}：利润表不可用，无法评估")
        return AnalysisResult(company=company, signals=signals, unevaluated=unevaluated)

    for rule_fn, rid in ((rule1_revenue_slowdown, "R1"), (rule2_revenue_up_profit_down, "R2")):
        result = rule_fn(std_income, company=company)
        signals.extend(result.signals)
        unevaluated.extend(result.unevaluated)

    if std_cashflow is None:
        unevaluated.append(f"R3 {RULE_NAMES['R3']}：现金流量表不可用，无法评估")
    else:
        result = rule3_cashflow_profit_divergence(std_income, std_cashflow, company=company)
        signals.extend(result.signals)
        unevaluated.extend(result.unevaluated)

    if std_balance is None:
        unevaluated.append(f"R4 {RULE_NAMES['R4']}：资产负债表不可用，无法评估")
    else:
        result = rule4_receivables_growth(std_income, std_balance, company=company)
        signals.extend(result.signals)
        unevaluated.extend(result.unevaluated)

    return AnalysisResult(company=company, signals=signals, unevaluated=unevaluated)


def signal_summary_frame(signals: list) -> pd.DataFrame:
    """信号汇总表：级别 / 规则 / 信号 / 报告期 / 触发条件。"""
    rows = [{
        "级别": LEVEL_CN.get(s.level, s.level),
        "规则": s.rule_id,
        "信号": s.name,
        "报告期": s.period,
        "触发条件": s.condition,
    } for s in signals]
    return pd.DataFrame(rows, columns=["级别", "规则", "信号", "报告期", "触发条件"])


def format_metric(key: str, value) -> str:
    """按指标键名含义选择格式：金额（元）/ 百分点 / 同比 / 状态 / 比率。"""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    if "状态" in key or "口径" in key:
        return str(value)
    if "元）" in key or "(元)" in key:
        return f"{float(value):,.0f}"
    if "百分点" in key:
        return f"{float(value):+.1f}"
    if "同比" in key:
        return f"{float(value):+.1%}"
    return f"{float(value):,.2f}"


def format_metric_lines(metrics: dict) -> list[str]:
    """Signal.metrics → 展示行（key：formatted value）。"""
    return [f"{key}：{format_metric(key, v)}" for key, v in metrics.items()]
