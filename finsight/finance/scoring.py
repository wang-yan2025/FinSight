"""投研评分模型：基本面 / 财务质量 / 技术面 / 风险 四维加权。"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field

import pandas as pd

from finsight.finance.indicators import yoy_series
from finsight.finance.signals_v2 import RiskAssessment, assess_risk
from finsight.finance.technical import TechnicalView

DEFAULT_WEIGHTS = {"fundamental": 0.30, "financial": 0.30, "technical": 0.20, "risk": 0.20}

RATING_CN = {-2: "卖出", -1: "减持", 0: "持有", 1: "增持", 2: "买入"}


@dataclass
class DimensionScore:
    name: str
    score: float          # 0–100
    weight: float
    detail: str = ""


@dataclass
class InvestmentScore:
    """加权总分 + 评级 + 各维明细。"""
    total: float = 50.0
    rating: int = 0
    rating_cn: str = "持有"
    dimensions: list[DimensionScore] = dc_field(default_factory=list)
    confidence: str = "中"   # 高/中/低
    rationale: str = ""


def _score_from_yoy(inc: pd.DataFrame, col: str) -> float:
    """简单评分：同比>15%=80, 0~15%=60, -10~0%=40, <-10%=20。"""
    out = yoy_series(inc, col, basis="cumulative")
    if out.empty:
        return 50.0
    last = out.sort_values("report_date").iloc[-1]
    if pd.isna(last["yoy"]):
        return 50.0
    v = last["yoy"]
    if v > 0.15:
        return 80.0
    if v > 0:
        return 60.0
    if v > -0.10:
        return 40.0
    return 20.0


def _last_valid(df, col="value", default=0.5):
    """从 apply_indicator 输出取最后一个非 NaN 值。"""
    if df is None or df.empty or col not in df.columns:
        return default
    vals = df[col].dropna()
    return float(vals.iloc[-1]) if not vals.empty else default


def _financial_quality_score(inc, bal) -> float:
    """财务质量：毛利率 + 净利率 + 资产负债率 + 流动比率。"""
    parts = []
    if inc is not None and not inc.empty:
        from finsight.finance.indicators import apply_indicator, gross_margin, net_margin
        gm = apply_indicator(inc, gross_margin, {"revenue": "revenue", "operate_cost": "operate_cost"})
        parts.append(min(_last_valid(gm, default=0.4) * 150, 100))
        nm = apply_indicator(inc, net_margin, {"net_profit": "net_profit", "revenue": "revenue"})
        parts.append(min(_last_valid(nm, default=0.1) * 400, 100))
    if bal is not None and not bal.empty:
        from finsight.finance.indicators import apply_indicator, debt_to_asset_ratio, current_ratio
        dar = apply_indicator(bal, debt_to_asset_ratio,
                              {"total_liabilities": "total_liabilities", "total_assets": "total_assets"})
        parts.append(max(0, 100 - _last_valid(dar, default=0.5) * 150))
        cr = apply_indicator(bal, current_ratio,
                             {"total_current_assets": "total_current_assets",
                              "total_current_liabilities": "total_current_liabilities"})
        parts.append(min(_last_valid(cr, default=1.0) * 40, 100))
    return sum(parts) / len(parts) if parts else 50.0


def _technical_score(view: TechnicalView) -> float:
    """技术面评分：趋势+MACD+RSI+KDJ 各贡献。"""
    s = 50.0
    if "上升" in view.trend or "多头" in view.trend:
        s += 15
    elif "下降" in view.trend or "空头" in view.trend:
        s -= 15
    if "金叉" in view.macd_signal or "多头" in view.macd_signal:
        s += 10
    elif "死叉" in view.macd_signal or "空头" in view.macd_signal:
        s -= 10
    if view.rsi14:
        if 40 <= view.rsi14 <= 60:
            s += 5
        elif view.rsi14 > 70:
            s -= 10
        elif view.rsi14 < 30:
            s += 8  # 超卖可能反弹
    if view.kdj_k and view.kdj_d:
        if view.kdj_k > view.kdj_d:
            s += 5
        else:
            s -= 5
    return max(0, min(100, s))


def _risk_score(risk: RiskAssessment) -> float:
    """风险评分：触发信号越多越低。"""
    if risk.level == "red":
        return 15.0
    if risk.level == "orange":
        return 35.0
    if risk.level == "yellow":
        return 60.0
    return 80.0


def compute_score(std_income, std_balance, std_cashflow,
                  technical: TechnicalView | None = None,
                  risk: RiskAssessment | None = None,
                  weights: dict | None = None) -> InvestmentScore:
    """四维加权评分。"""
    w = {**DEFAULT_WEIGHTS, **(weights or {})}

    # 基本面（营收+利润增速的均值）
    rev_s = _score_from_yoy(std_income, "revenue") if std_income is not None else 50
    np_s = _score_from_yoy(std_income, "net_profit") if std_income is not None else 50
    fund = (rev_s + np_s) / 2

    fin = _financial_quality_score(std_income, std_balance)
    tech = _technical_score(technical) if technical else 50.0
    risk_s = _risk_score(risk) if risk else 50.0

    dims = [
        DimensionScore("基本面", fund, w["fundamental"],
                       f"营收增速分={rev_s:.0f}，利润增速分={np_s:.0f}"),
        DimensionScore("财务质量", fin, w["financial"], ""),
        DimensionScore("技术面", tech, w["technical"],
                       technical.summary if technical else "K线数据不可用"),
        DimensionScore("风险", risk_s, w["risk"],
                       risk.summary if risk else "未评估"),
    ]
    total = sum(d.score * d.weight for d in dims)
    rating = 2 if total >= 70 else (1 if total >= 58 else (0 if total >= 45 else (-1 if total >= 35 else -2)))
    conf = "高" if total >= 70 or total <= 35 else ("低" if 45 <= total <= 55 else "中")
    rationale = f"加权总分 {total:.0f}（基本面{fund:.0f}×{w['fundamental']:.0%}"
    rationale += f" + 财务{fin:.0f}×{w['financial']:.0%}"
    rationale += f" + 技术{tech:.0f}×{w['technical']:.0%}"
    rationale += f" + 风险{risk_s:.0f}×{w['risk']:.0%}）"

    return InvestmentScore(total=round(total, 1), rating=rating,
                           rating_cn=RATING_CN[rating], dimensions=dims,
                           confidence=conf, rationale=rationale)
