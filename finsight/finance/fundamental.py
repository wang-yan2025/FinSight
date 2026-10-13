"""三层基本面分析框架：宏观层 → 行业层 → 公司层。

每层输出"事实 + 信号 + 对结论的贡献"，供备忘录与评分模型复用。
数据源：akshare 宏观指标（PMI 等）+ 公司所属行业 + 现有财务引擎。
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field

import pandas as pd

from finsight.finance.signals_v2 import RiskAssessment


@dataclass
class LayerFact:
    """单条基本面事实。"""
    layer: str          # 宏观 / 行业 / 公司
    label: str
    value: str
    signal: str = ""    # 正面 / 负面 / 中性
    source: str = ""


@dataclass
class FundamentalView:
    """三层基本面综合观点。"""
    macro_facts: list[LayerFact] = dc_field(default_factory=list)
    industry_facts: list[LayerFact] = dc_field(default_factory=list)
    company_facts: list[LayerFact] = dc_field(default_factory=list)
    macro_summary: str = ""
    industry_summary: str = ""
    company_summary: str = ""
    overall: str = ""

    def all_facts(self) -> list[LayerFact]:
        return self.macro_facts + self.industry_facts + self.company_facts


def build_fundamental(macro: dict, profile: dict, risk: RiskAssessment,
                      std_income=None) -> FundamentalView:
    """构建三层基本面观点（纯函数，输入为已获取的数据）。"""
    view = FundamentalView()

    # ---- 宏观层 ----
    if macro:
        pmi = macro.get("PMI", {})
        if pmi.get("值") is not None:
            v = pmi["值"]
            sig = "正面" if v >= 50.5 else ("负面" if v < 49.5 else "中性")
            view.macro_facts.append(LayerFact(
                "宏观", f"制造业 PMI（{pmi.get('日期', '')}）",
                f"{v}", sig, "东方财富数据中心"))
            view.macro_summary = f"PMI {v}（{'扩张' if v >= 50 else '收缩'}区间）"
    if not view.macro_summary:
        view.macro_summary = "宏观数据待补充（获取失败或离线模式）"
        view.macro_facts.append(LayerFact("宏观", "宏观数据", "待补充", "中性", ""))

    # ---- 行业层 ----
    industry = profile.get("行业") or ""
    if industry:
        view.industry_facts.append(LayerFact(
            "行业", "所属行业", industry, "中性", "东方财富个股信息"))
        view.industry_summary = f"公司属于{industry}行业"
    else:
        view.industry_facts.append(LayerFact("行业", "所属行业", "待补充", "中性", ""))
        view.industry_summary = "行业信息待补充"
    # 行业景气度提示（轻量版：不拉行业指数，标注为待人工核实）
    view.industry_facts.append(LayerFact(
        "行业", "行业景气度", "待人工核实（需引入行业指数与同业数据）", "中性", ""))

    # ---- 公司层 ----
    if risk:
        view.company_facts.append(LayerFact(
            "公司", "风险预警", f"{risk.level_cn}（{risk.triggered_count}/7 项）",
            "负面" if risk.triggered_count >= 2 else ("中性" if risk.triggered_count == 1 else "正面"),
            "S1–S7 引擎"))
        view.company_summary = risk.summary
    if std_income is not None and not std_income.empty:
        latest = std_income.sort_values("report_date").iloc[-1]
        if pd.notna(latest.get("revenue")) and latest["revenue"] > 0:
            view.company_facts.append(LayerFact(
                "公司", "最新营收", f"{latest['revenue'] / 1e8:.1f} 亿元", "中性", "利润表"))
    view.company_facts.append(LayerFact(
        "公司", "公司治理/护城河", "待人工核实（需引入公告与研报数据）", "中性", ""))

    # ---- 总评 ----
    pos = sum(1 for f in view.all_facts() if f.signal == "正面")
    neg = sum(1 for f in view.all_facts() if f.signal == "负面")
    if neg >= 2:
        view.overall = "基本面偏弱（多项负面信号）"
    elif neg == 1 and pos == 0:
        view.overall = "基本面中性偏弱"
    elif pos >= 2:
        view.overall = "基本面偏强"
    else:
        view.overall = "基本面中性"

    return view
