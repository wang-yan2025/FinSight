"""阶段 D+E 测试：三层基本面、备忘录新章节、Agent 工具调用。"""
import json
import pandas as pd
import pytest

from finsight.finance.conclusion import build_conclusion, DISCLAIMER
from finsight.finance.fundamental import FundamentalView, LayerFact, build_fundamental
from finsight.finance.field_mapping import REPORT_DATE_FIELD, standardize_statement
from finsight.finance.scoring import compute_score
from finsight.finance.signals_v2 import assess_risk
from finsight.finance.technical import TechnicalView
from finsight.memo.evidence import build_evidence_book
from finsight.memo.skeleton import SECTION_ORDER, SECTION_TITLES, build_memo_skeleton

DATES = [f"{y}-{m:02d}-{d:02d} 00:00:00" for y in (2023, 2024)
         for m, d in ((3, 31), (6, 30), (9, 30), (12, 31))]
_cum = lambda xs: list(__import__("itertools").accumulate(xs))
E8 = lambda lst: [v * 1e8 for v in lst]


def _frames():
    inc = standardize_statement("income", pd.DataFrame({
        REPORT_DATE_FIELD: DATES,
        "OPERATE_INCOME": E8(_cum([100]*4) + _cum([120,110,105,95])),
        "OPERATE_COST": E8(_cum([60]*4) + _cum([70,66,63,57])),
        "NETPROFIT": E8(_cum([50]*4) + _cum([10,-5,-10,-15])),
    }))
    return inc


# ---------------- 三层基本面 ----------------

def test_fundamental_layers():
    risk = assess_risk(_frames(), None, None)
    view = build_fundamental({"PMI": {"值": 51.2, "日期": "2026-09"}},
                              {"行业": "白酒"}, risk, _frames())
    assert len(view.macro_facts) >= 1
    assert len(view.industry_facts) >= 2
    assert len(view.company_facts) >= 2
    assert "PMI" in view.macro_summary
    assert "白酒" in view.industry_summary
    assert view.overall


def test_fundamental_empty_macro():
    risk = assess_risk(_frames(), None, None)
    view = build_fundamental({}, {}, risk, None)
    assert "待补充" in view.macro_summary
    assert "待补充" in view.industry_summary


# ---------------- 备忘录新章节 ----------------

def _build_v2_memo():
    inc = _frames()
    book = build_evidence_book("V2TEST", inc, None, None)
    risk = assess_risk(inc, None, None)
    tech = TechnicalView(trend="上升趋势", macd_signal="多头", rsi14=55,
                         rsi_signal="中性", summary="均线多头排列")
    score = compute_score(inc, None, None, technical=tech, risk=risk)
    conclusion = build_conclusion(score, risk, tech)
    fundamental = build_fundamental({"PMI": {"值": 50.5, "日期": "2026-09"}},
                                     {"行业": "测试"}, risk, inc)
    return build_memo_skeleton("V2TEST", book, conclusion=conclusion,
                               fundamental=fundamental, technical=tech,
                               risk_v2=risk, score=score)


def test_memo_v2_section_order():
    memo = _build_v2_memo()
    keys = [s.key for s in memo.sections]
    assert keys == SECTION_ORDER
    assert "conclusion_card" in keys
    assert "fundamental" in keys
    assert "technical" in keys
    assert "reverse_validation" in keys


def test_memo_v2_conclusion_card_content():
    memo = _build_v2_memo()
    cc = memo.section("conclusion_card")
    assert cc is not None
    text = " ".join(cc.facts)
    assert "投资评级" in text
    assert "免责" in text or "不构成" in text


def test_memo_v2_technical_section():
    memo = _build_v2_memo()
    tech = memo.section("technical")
    text = " ".join(tech.facts)
    assert "趋势" in text
    assert "MACD" in text
    assert "RSI" in text


def test_memo_v2_fundamental_section():
    memo = _build_v2_memo()
    fund = memo.section("fundamental")
    text = " ".join(fund.facts)
    assert "宏观层" in text
    assert "行业层" in text
    assert "公司层" in text


def test_memo_v2_reverse_validation():
    memo = _build_v2_memo()
    rv = memo.section("reverse_validation")
    text = " ".join(rv.facts)
    assert "不成立" in text or "重新评估" in text
    assert "营收" in text


def test_memo_v2_backward_compat():
    """不传 V2.0 参数时仍可生成（降级为 V1 骨架 + 新章节占位）。"""
    inc = _frames()
    book = build_evidence_book("COMPAT", inc, None, None)
    memo = build_memo_skeleton("COMPAT", book)
    keys = [s.key for s in memo.sections]
    assert "conclusion_card" in keys  # 章节存在，内容为"待生成"
    cc = memo.section("conclusion_card")
    assert any("待生成" in f or "待补充" in f for f in cc.facts)


# ---------------- Agent（离线 mock） ----------------

def test_agent_tool_definitions():
    from finsight.agent import build_tools
    risk = assess_risk(_frames(), None, None)
    tech = TechnicalView(trend="上升趋势")
    inc = _frames()
    book = build_evidence_book("AGT", inc, None, None)
    memo = build_memo_skeleton("AGT", book)
    score = compute_score(inc, None, None)
    tools = build_tools(risk, tech, score, memo)
    assert len(tools) == 4
    names = {t["function"]["name"] for t in tools}
    assert names == {"get_risk_signals", "get_technical_analysis",
                     "get_investment_score", "get_financial_evidence"}


def test_agent_tool_execution():
    from finsight.agent import _execute_tool
    risk = assess_risk(_frames(), None, None)
    tech = TechnicalView(trend="上升趋势", macd_signal="多头", rsi14=55,
                         rsi_signal="中性", summary="多头")
    inc = _frames()
    book = build_evidence_book("AGT", inc, None, None)
    memo = build_memo_skeleton("AGT", book)
    score = compute_score(inc, None, None, technical=tech, risk=risk)

    r1 = _execute_tool("get_risk_signals", risk, tech, score, memo)
    assert "预警" in r1 and "S" in r1
    r2 = _execute_tool("get_technical_analysis", risk, tech, score, memo)
    assert "趋势" in r2 and "MACD" in r2
    r3 = _execute_tool("get_investment_score", risk, tech, score, memo)
    assert "总分" in r3
    r4 = _execute_tool("get_financial_evidence", risk, tech, score, memo)
    assert "E-" in r4
    r5 = _execute_tool("unknown_tool", risk, tech, score, memo)
    assert "未知" in r5


def test_agent_unconfigured():
    from finsight.agent import run_agent
    from finsight.memo.llm import LLMConfig
    inc = _frames()
    book = build_evidence_book("AGT", inc, None, None)
    memo = build_memo_skeleton("AGT", book)
    risk = assess_risk(inc, None, None)
    score = compute_score(inc, None, None, risk=risk)
    result = run_agent(LLMConfig(), memo, risk, None, score)
    assert result.status == "api_error"  # 未配置 → 请求失败 → api_error
