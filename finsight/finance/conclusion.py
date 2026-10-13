"""明确结论卡：评级/置信度/反向验证条件/持有期（PRD 模块三核心输出）。

作为统一投研决策结论引擎，供财务分析、备忘录、核验三模块复用。
所有结论附固定免责声明。
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field

from finsight.finance.scoring import InvestmentScore, RATING_CN
from finsight.finance.signals_v2 import RiskAssessment
from finsight.finance.technical import TechnicalView

DISCLAIMER = (
    "本结论由 FinSight 智研投基于公开数据的确定性规则引擎生成，"
    "仅供研究参考，不构成任何投资建议或买卖依据。"
    "投资有风险，决策需独立判断。"
)


@dataclass
class ReverseCondition:
    """反向验证条件：什么情况下投资逻辑不成立。"""
    indicator: str
    threshold: str
    description: str


@dataclass
class ConclusionCard:
    """结构化结论对象（三模块统一输出格式）。"""
    rating: str = "持有"
    confidence: str = "中"
    score_total: float = 50.0
    score_detail: str = ""
    technical_summary: str = ""
    risk_level: str = "无预警"
    risk_summary: str = ""
    reverse_conditions: list[ReverseCondition] = dc_field(default_factory=list)
    holding_period: str = "中期（6-12 个月）"
    key_levels: dict = dc_field(default_factory=dict)  # 支撑/压力
    rationale: str = ""
    disclaimers: list[str] = dc_field(default_factory=lambda: [DISCLAIMER])

    def to_dict(self) -> dict:
        return {
            "评级": self.rating, "置信度": self.confidence,
            "总分": self.score_total, "评分明细": self.score_detail,
            "技术面": self.technical_summary, "风险等级": self.risk_level,
            "反向验证条件": [{"指标": r.indicator, "阈值": r.threshold, "说明": r.description}
                             for r in self.reverse_conditions],
            "建议持有期": self.holding_period,
            "关键价位": self.key_levels,
            "依据": self.rationale,
            "免责声明": self.disclaimers,
        }


def build_conclusion(score: InvestmentScore, risk: RiskAssessment,
                     technical: TechnicalView | None = None) -> ConclusionCard:
    """从评分+风险+技术面构建结论卡（纯函数）。"""
    card = ConclusionCard(
        rating=score.rating_cn,
        confidence=score.confidence,
        score_total=score.total,
        score_detail=score.rationale,
        risk_level=risk.level_cn,
        risk_summary=risk.summary,
    )
    if technical:
        card.technical_summary = technical.summary
        card.key_levels = {
            "支撑位": technical.support,
            "压力位": technical.resistance,
            "强支撑": technical.strong_support,
            "强压力": technical.strong_resistance,
        }
        # 短期信号强则建议短期
        if "金叉" in technical.macd_signal or "超卖" in technical.rsi_signal:
            card.holding_period = "短期（1-3 个月，技术面反弹）"
        elif "死叉" in technical.macd_signal or "超买" in technical.rsi_signal:
            card.holding_period = "观望（技术面偏弱，等待企稳）"

    # 反向验证条件（基于评分维度与风险信号反向推导）
    card.reverse_conditions = _derive_reverse_conditions(score, risk)

    parts = [f"综合评分 {score.total:.0f} 分（{score.rating_cn}）"]
    if risk.triggered_count > 0:
        parts.append(f"风险预警 {risk.level_cn}（{risk.triggered_count} 项信号）")
    if technical and technical.trend:
        parts.append(f"技术面{technical.trend}")
    card.rationale = "；".join(parts)

    return card


def _derive_reverse_conditions(score: InvestmentScore, risk: RiskAssessment) -> list[ReverseCondition]:
    conditions = []
    # 基本面反转
    conditions.append(ReverseCondition(
        "营收同比增速",
        "连续 2 个季度 < 0",
        "营收负增长持续将动摇成长性假设，投资逻辑不成立"))
    conditions.append(ReverseCondition(
        "净利润同比增速",
        "连续 2 个季度 < -20%",
        "利润大幅且持续下滑，盈利假设失效"))
    # 风险信号
    if risk.triggered_count > 0:
        conditions.append(ReverseCondition(
            "风险预警级别",
            "升至红色（≥3 项信号）",
            f"当前 {risk.level_cn}（{risk.triggered_count} 项），若升至红色需重新审视"))
    # 技术面
    conditions.append(ReverseCondition(
        "股价关键支撑位",
        "跌破强支撑并持续 3 日",
        "技术面破位，短期走势逻辑失效"))
    return conditions
