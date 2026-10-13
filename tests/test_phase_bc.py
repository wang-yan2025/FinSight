"""阶段 B/C 测试：QoQ 环比、评分模型、结论卡。"""
import pandas as pd
import pytest

from finsight.finance.conclusion import (
    DISCLAIMER,
    ReverseCondition,
    build_conclusion,
)
from finsight.finance.field_mapping import REPORT_DATE_FIELD, standardize_statement
from finsight.finance.indicators import qoq_series
from finsight.finance.scoring import DEFAULT_WEIGHTS, InvestmentScore, compute_score
from finsight.finance.signals_v2 import assess_risk
from finsight.finance.technical import TechnicalView

DATES = [f"{y}-{m:02d}-{d:02d} 00:00:00" for y in (2023, 2024)
         for m, d in ((3, 31), (6, 30), (9, 30), (12, 31))]
_cum = lambda xs: list(__import__("itertools").accumulate(xs))
E8 = lambda lst: [v * 1e8 for v in lst]


def _inc(rev_q, np_q):
    rev_cum = _cum(rev_q[0]) + _cum(rev_q[1])
    np_cum = _cum(np_q[0]) + _cum(np_q[1])
    return standardize_statement("income", pd.DataFrame({
        REPORT_DATE_FIELD: DATES,
        "OPERATE_INCOME": E8(rev_cum),
        "OPERATE_COST": E8([v * 0.6 for v in rev_cum]),
        "NETPROFIT": E8(np_cum),
    }))


# ---------------- QoQ 环比 ----------------

def test_qoq_basic():
    # 2024 单季: 100→120→110→130；QoQ Q2=+20%, Q3=-8.3%, Q4=+18.2%
    inc = _inc([[100, 100, 100, 100], [100, 120, 110, 130]],
               [[50, 50, 50, 50], [50, 60, 55, 65]])
    out = qoq_series(inc, "revenue")
    fy24 = out[out["year"] == 2024].sort_values("quarter").reset_index(drop=True)
    assert len(fy24) == 4
    # iloc[0]=Q1, iloc[1]=Q2, iloc[2]=Q3
    # Q2 环比 = (120-100)/100 = 20%
    assert fy24.iloc[1]["qoq"] == pytest.approx(0.20, abs=0.01)
    # Q3 环比 = (110-120)/120 = -8.3%
    assert fy24.iloc[2]["qoq"] == pytest.approx(-0.083, abs=0.01)


def test_qoq_q1_crosses_year():
    # 2024Q1 vs 2023Q4
    inc = _inc([[100, 100, 100, 80], [120, 120, 120, 120]],
               [[50, 50, 50, 40], [60, 60, 60, 60]])
    out = qoq_series(inc, "revenue")
    q1_2024 = out[(out["year"] == 2024) & (out["quarter"] == 1)]
    assert len(q1_2024) == 1
    # 120 vs 80 = +50%
    assert q1_2024.iloc[0]["qoq"] == pytest.approx(0.50, abs=0.01)


def test_qoq_missing_prev():
    # 首期无上一季度 → reason 标注
    inc = _inc([[100, 100, 100, 100], [100, 100, 100, 100]],
               [[50, 50, 50, 50], [50, 50, 50, 50]])
    out = qoq_series(inc, "revenue")
    first = out.iloc[0]
    assert pd.isna(first["qoq"])
    assert "缺失" in first["reason"]


# ---------------- 评分模型 ----------------

def _tech_bullish():
    v = TechnicalView()
    v.trend = "上升趋势（多头排列）"
    v.macd_signal = "多头（DIF>DEA）"
    v.rsi14 = 55.0
    v.kdj_k, v.kdj_d = 70, 60
    v.summary = "均线上升趋势；MACD 多头"
    return v


def _tech_bearish():
    v = TechnicalView()
    v.trend = "下降趋势（空头排列）"
    v.macd_signal = "空头（DIF<DEA）"
    v.rsi14 = 25.0
    v.kdj_k, v.kdj_d = 30, 50
    v.summary = "均线下降趋势；MACD 空头；RSI 超卖"
    return v


def test_scoring_dimensions():
    inc = _inc([[100, 100, 100, 100], [110, 115, 120, 125]],
               [[50, 50, 50, 50], [55, 58, 60, 65]])
    risk = assess_risk(inc, None, None)
    tech = _tech_bullish()
    score = compute_score(inc, None, None, technical=tech, risk=risk)
    assert len(score.dimensions) == 4
    names = [d.name for d in score.dimensions]
    assert names == ["基本面", "财务质量", "技术面", "风险"]
    assert 0 <= score.total <= 100
    assert score.rating in (-2, -1, 0, 1, 2)
    assert score.rating_cn


def test_scoring_bullish_beats_bearish():
    inc = _inc([[100] * 4, [110, 115, 120, 125]], [[50] * 4, [55, 58, 60, 65]])
    risk = assess_risk(inc, None, None)
    s_bull = compute_score(inc, None, None, technical=_tech_bullish(), risk=risk)
    s_bear = compute_score(inc, None, None, technical=_tech_bearish(), risk=risk)
    assert s_bull.total > s_bear.total


def test_scoring_custom_weights():
    """权重变化会改变各维度在总分中的占比。"""
    inc = _inc([[100] * 4, [110, 115, 120, 125]], [[50] * 4, [55, 58, 60, 65]])
    risk = assess_risk(inc, None, None)
    s1 = compute_score(inc, None, None, technical=_tech_bullish(), risk=risk)
    s2 = compute_score(inc, None, None, technical=_tech_bullish(), risk=risk,
                       weights={"fundamental": 0.5, "financial": 0.2,
                                "technical": 0.2, "risk": 0.1})
    # 验证权重正确传递
    d1 = {d.name: d.weight for d in s1.dimensions}
    d2 = {d.name: d.weight for d in s2.dimensions}
    assert d1["基本面"] == 0.30 and d2["基本面"] == 0.50
    assert d1["风险"] == 0.20 and d2["风险"] == 0.10
    # 验证加权计算（手动复核）
    expected2 = sum(d.score * d.weight for d in s2.dimensions)
    assert s2.total == pytest.approx(expected2, abs=0.1)


# ---------------- 结论卡 ----------------

def test_conclusion_card_fields():
    inc = _inc([[100] * 4, [110, 115, 120, 125]], [[50] * 4, [55, 58, 60, 65]])
    risk = assess_risk(inc, None, None)
    tech = _tech_bullish()
    score = compute_score(inc, None, None, technical=tech, risk=risk)
    card = build_conclusion(score, risk, tech)
    assert card.rating in ("买入", "增持", "持有", "减持", "卖出")
    assert card.confidence in ("高", "中", "低")
    assert card.score_total > 0
    assert len(card.reverse_conditions) >= 3  # 至少 3 条反向条件
    assert DISCLAIMER in card.disclaimers[0] or "不构成" in card.disclaimers[0]
    assert card.technical_summary


def test_conclusion_reverse_conditions_content():
    inc = _inc([[100] * 4, [110, 115, 120, 125]], [[50] * 4, [55, 58, 60, 65]])
    risk = assess_risk(inc, None, None)
    score = compute_score(inc, None, None)
    card = build_conclusion(score, risk)
    for rc in card.reverse_conditions:
        assert rc.indicator and rc.threshold and rc.description
    # 至少包含营收和净利润反向条件
    indicators = " ".join(rc.indicator for rc in card.reverse_conditions)
    assert "营收" in indicators
    assert "净利润" in indicators


def test_conclusion_to_dict():
    inc = _inc([[100] * 4, [110, 115, 120, 125]], [[50] * 4, [55, 58, 60, 65]])
    risk = assess_risk(inc, None, None)
    score = compute_score(inc, None, None)
    card = build_conclusion(score, risk)
    d = card.to_dict()
    assert "评级" in d and "免责声明" in d
    assert isinstance(d["反向验证条件"], list)
    assert len(d["反向验证条件"]) >= 3
