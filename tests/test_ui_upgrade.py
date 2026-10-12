"""UI 升级测试：导航、流程状态推导、KPI 组装、样式安全性。"""
import pandas as pd
import pytest

import finsight.ui.styles as styles
from finsight.finance.field_mapping import REPORT_DATE_FIELD, standardize_statement
from finsight.ui.components import esc
from finsight.ui.flow import STEPS, workflow_states
from finsight.ui.view import latest_kpis


# ---------------- 导航 ----------------

def test_nav_pages_defined():
    import importlib.util
    spec = importlib.util.spec_from_file_location("app_nav", "app.py")
    src = open("app.py", encoding="utf-8").read()
    for name in ("财务分析中心", "投资备忘录", "独立报告核验", "智能投研工作台"):
        assert name in src
    assert "Financial Intelligence Platform" in src


# ---------------- 流程状态推导 ----------------

def test_workflow_states_progression():
    # 初始（仅数据加载后）
    s = workflow_states(True, False, False, False)
    assert s == ["done", "active", "todo", "todo", "todo"]
    # V1 生成
    s = workflow_states(True, True, False, False)
    assert s == ["done", "done", "active", "todo", "todo"]
    # 首轮核验完成
    s = workflow_states(True, True, True, False)
    assert s == ["done", "done", "done", "active", "todo"]
    # 全部完成
    s = workflow_states(True, True, True, True)
    assert s == ["done"] * 5
    assert len(s) == len(STEPS)


def test_workflow_states_error_marks_active():
    s = workflow_states(True, True, False, False, error=True)
    assert s[2] == "error" and s[0] == "done"


# ---------------- KPI 组装 ----------------

def _income():
    dates = [f"{y}-{m:02d}-{d:02d} 00:00:00" for y in (2023, 2024)
             for m, d in ((3, 31), (6, 30), (9, 30), (12, 31))]
    cum = lambda xs: list(__import__("itertools").accumulate(xs))
    e8 = lambda lst: [v * 1e8 for v in lst]
    return standardize_statement("income", pd.DataFrame({
        REPORT_DATE_FIELD: dates,
        "OPERATE_INCOME": e8(cum([100] * 4) + cum([120, 110, 90, 95])),
        "OPERATE_COST": e8(cum([60] * 4) + cum([70, 66, 54, 57])),
        "NETPROFIT": e8(cum([50] * 4) + cum([10, -5, -10, -15])),
    }))


def test_latest_kpis_values_and_badges():
    cards = latest_kpis(_income(), None, None)
    labels = {c["label"] for c in cards}
    assert {"营业收入", "净利润", "毛利率"} <= labels
    rev = next(c for c in cards if c["label"] == "营业收入")
    assert rev["value"] == "415.0" and rev["unit"] == "亿元"     # 2024FY 累计 415 亿
    assert "2024Q4" in rev["sub"]
    np_ = next(c for c in cards if c["label"] == "净利润")
    assert np_["badge"]["text"].startswith("由盈转亏")            # 状态分类进入徽标


def test_latest_kpis_empty_without_income():
    assert latest_kpis(None, None, None) == []


# ---------------- 样式安全 ----------------

def test_css_no_external_resources_and_reduced_motion():
    css = styles.global_css()
    assert "prefers-reduced-motion" in css
    for banned in ("http://", "https://", "@import", "url("):
        assert banned not in css, banned
    # 设计令牌存在
    for token in ("#376CFB", "#15B8A6", "#101E35"):
        assert token in css


def test_esc_prevents_html_injection():
    bad = '<script>alert("x")</script>'
    out = esc(bad)
    assert "<script>" not in out and "&lt;script&gt;" in out
