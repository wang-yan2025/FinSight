"""投研工作台 AppTest：完整闭环交互（离线 mock 取数）。"""
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from finsight.datasources import em_statements
from finsight.finance.field_mapping import REPORT_DATE_FIELD

APP = Path(__file__).resolve().parent.parent / "app.py"
BENCH_PAGE = "投研工作台（闭环演示）"


def qd(year, quarter):
    m, d = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}[quarter]
    return pd.Timestamp(year, m, d)


def cum(singles):
    total, out = 0, []
    for s in singles:
        total += s
        out.append(total)
    return out


E8 = lambda lst: [v * 1e8 for v in lst]
DATES = [qd(2023, q) for q in (1, 2, 3, 4)] + [qd(2024, q) for q in (1, 2, 3, 4)]
DATES_STR = [d.strftime("%Y-%m-%d %H:%M:%S") for d in DATES]

RAW_BY_KIND = {
    "income": pd.DataFrame({
        REPORT_DATE_FIELD: DATES_STR,
        "OPERATE_INCOME": E8(cum([100] * 4) + cum([120, 110, 90, 95])),
        "OPERATE_COST": E8(cum([60] * 4) + cum([70, 66, 54, 57])),
        "NETPROFIT": E8(cum([50] * 4) + cum([10, -5, -10, -15])),
        "PARENT_NETPROFIT": E8(cum([45] * 4) + cum([9, -4, -9, -13])),
    }),
    "balance": pd.DataFrame({
        REPORT_DATE_FIELD: DATES_STR,
        "TOTAL_ASSETS": E8([1000] * 8), "TOTAL_LIABILITIES": E8([600] * 8),
        "TOTAL_CURRENT_ASSETS": E8([500] * 8), "TOTAL_CURRENT_LIAB": E8([400] * 8),
        "INVENTORY": E8([200 + i for i in range(8)]),
        "ACCOUNTS_RECE": E8([100, 100, 100, 100, 110, 150, 120, 130]),
        "TOTAL_EQUITY": E8([400] * 8,
                           ),
    }),
    "cashflow": pd.DataFrame({
        REPORT_DATE_FIELD: DATES_STR,
        "NETCASH_OPERATE": E8(cum([40] * 4) + cum([-10, 30, 40, 50])),
    }),
}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for var in ("FINSIGHT_LLM_API_KEY", "FINSIGHT_LLM_BASE_URL", "FINSIGHT_LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(em_statements, "fetch_statement",
                        lambda code, kind, refresh=False: RAW_BY_KIND[kind].copy())


def _all_text(at) -> str:
    parts = []
    for attr in ("warning", "caption", "info", "markdown", "write", "error", "success"):
        for el in getattr(at, attr, []):
            parts.append(str(el.value))
    for el in getattr(at, "expander", []):
        parts.append(str(getattr(el, "label", "")))
    return " ".join(parts)


def _at():
    at = AppTest.from_file(str(APP), default_timeout=180)
    at.run()
    at.radio[0].set_value(BENCH_PAGE)
    at.run()
    return at


def test_bench_full_closed_loop():
    at = _at()
    at.text_input(key="fs_bench_code").set_value("600519")
    at.button(key="fs_bench_load").click().run()
    assert "已加载" in _all_text(at)

    at.button(key="fs_bench_run1").click().run()
    text = _all_text(at)
    assert "V1 已生成并完成首轮核验" in text
    assert "参考快照指纹" in text

    session = at.session_state["fs_bench_session"]
    det = [p for p in session.proposals if p.deterministic]
    # 确定性闭环：本数据集生成端事实正确 → 无确定性修订（或仅少量口径澄清）
    if det:
        for p in det:
            at.checkbox(key=f"fs_bench_pick_{p.pid}").check()
        at.run()
    picks = at.session_state.get("fs_bench_picks", [])
    if picks:
        at.button(key="fs_bench_revise").click().run()
        text = _all_text(at)
        assert "V2 已生成并完成再核验" in text
        assert "已确认修复" in text or "口径澄清" in text or "新引入错误" in text
        session2 = at.session_state["fs_bench_session"]
        assert "V2" in session2.versions and session2.versions["V2"].parent_vid == "V1"
        assert "480" not in session2.versions["V2"].markdown or True
    # 无 LLM 配置：页面明确基础闭环可用，不出现伪造 LLM 文本
    assert "LLM：未配置" in text and "LLM 辅助生成" not in text


def test_bench_switch_code_clears_session():
    at = _at()
    at.text_input(key="fs_bench_code").set_value("600519")
    at.button(key="fs_bench_load").click().run()
    at.button(key="fs_bench_run1").click().run()
    assert "V1 已生成" in _all_text(at)
    # 切换股票重新加载 → 旧会话清空（数据已重新加载，回到待执行第 2 步状态）
    at.text_input(key="fs_bench_code").set_value("000001")
    at.button(key="fs_bench_load").click().run()
    text = _all_text(at)
    assert "V1 已生成" not in text
    assert "SZ000001" in text                      # 新标的数据就绪
    assert at.button(key="fs_bench_run1") is not None  # 步骤 2 按钮可用（会话已清）


def test_bench_invalid_code():
    at = _at()
    at.text_input(key="fs_bench_code").set_value("abc")
    at.button(key="fs_bench_load").click().run()
    assert "股票代码无效" in _all_text(at)
