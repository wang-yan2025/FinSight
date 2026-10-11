"""核验页面 AppTest：离线 mock 取数，覆盖入口 A 端到端与状态切换。"""
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from finsight.datasources import em_statements
from finsight.finance.field_mapping import REPORT_DATE_FIELD

APP = Path(__file__).resolve().parent.parent / "app.py"
VERIFY_PAGE = "研报纠错（方向5）"
MEMO_PAGE = "买方备忘录（方向6）"


def qd(year, quarter):
    m, d = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}[quarter]
    return pd.Timestamp(year, m, d)


def cum(singles):
    total, out = 0, []
    for s in singles:
        total += s
        out.append(total)
    return out


e8 = lambda lst: [v * 1e8 for v in lst]
DATES = [qd(2023, q) for q in (1, 2, 3, 4)] + [qd(2024, q) for q in (1, 2, 3, 4)]
DATES_STR = [d.strftime("%Y-%m-%d %H:%M:%S") for d in DATES]

RAW_BY_KIND = {
    "income": pd.DataFrame({
        REPORT_DATE_FIELD: DATES_STR,
        "OPERATE_INCOME": e8(cum([100] * 4) + cum([120, 110, 90, 95])),
        "OPERATE_COST": e8(cum([60] * 4) + cum([70, 66, 54, 57])),
        "NETPROFIT": e8(cum([50] * 4) + cum([10, -5, -10, -15])),
        "PARENT_NETPROFIT": e8(cum([45] * 4) + cum([9, -4, -9, -13])),
    }),
    "balance": pd.DataFrame({
        REPORT_DATE_FIELD: DATES_STR,
        "TOTAL_ASSETS": e8([1000] * 8), "TOTAL_LIABILITIES": e8([600] * 8),
        "TOTAL_CURRENT_ASSETS": e8([500] * 8), "TOTAL_CURRENT_LIAB": e8([400] * 8),
        "INVENTORY": e8([200 + i for i in range(8)]),
        "ACCOUNTS_RECE": e8([100, 100, 100, 100, 110, 150, 120, 130]),
        "TOTAL_EQUITY": e8([400] * 8),
    }),
    "cashflow": pd.DataFrame({
        REPORT_DATE_FIELD: DATES_STR,
        "NETCASH_OPERATE": e8(cum([40] * 4) + cum([-10, 30, 40, 50])),
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
    for el in getattr(at, "exception", []):
        parts.append(str(el.value))
    return " ".join(parts)


def _generate_memo(at):
    at.radio[0].set_value(MEMO_PAGE)
    at.run()
    at.text_input(key="fs_memo_code").set_value("600519")
    at.button(key="fs_memo_load").click().run()
    at.button(key="fs_memo_generate").click().run()
    assert "基础备忘录已生成" in _all_text(at)


def test_verify_memo_end_to_end():
    at = AppTest.from_file(str(APP), default_timeout=180)
    at.run()
    _generate_memo(at)
    at.radio[0].set_value(VERIFY_PAGE)
    at.run()
    assert "尚未生成投资备忘录" not in _all_text(at)  # 备忘录在会话中
    assert "SH600519" in _all_text(at)
    at.button(key="fs_verify_run").click().run()
    text = _all_text(at)
    assert "核验完成" in text
    assert "通过" in text                       # 备忘录正确事实应通过
    assert "快照指纹" in text
    # 备忘录的确定性事实（如收入 415 亿、由盈转亏）应为通过
    assert any(str(getattr(e, "label", "")).startswith("通过｜") for e in at.expander)
    # 未自动修改原始报告 / 下载入口存在
    assert "不会自动修改原始报告" in text


def test_verify_requires_memo_or_file():
    at = AppTest.from_file(str(APP), default_timeout=120)
    at.run()
    at.radio[0].set_value(VERIFY_PAGE)
    at.run()
    text = _all_text(at)
    assert "尚未生成投资备忘录" in text  # 无备忘录时入口 A 给出引导


def test_verify_stale_result_cleared_on_context_change():
    at = AppTest.from_file(str(APP), default_timeout=180)
    at.run()
    _generate_memo(at)
    at.radio[0].set_value(VERIFY_PAGE)
    at.run()
    at.button(key="fs_verify_run").click().run()
    assert "核验完成" in _all_text(at)
    # 修改股票代码（上下文变化）→ 旧核验结果失效，回到待执行提示
    at.text_input(key="fs_verify_code").set_value("000001")
    at.run()
    text = _all_text(at)
    assert "核验完成" not in text
    assert "开始核验" in text


def test_verify_invalid_code_errors():
    at = AppTest.from_file(str(APP), default_timeout=180)
    at.run()
    _generate_memo(at)
    at.radio[0].set_value(VERIFY_PAGE)
    at.run()
    at.text_input(key="fs_verify_code").set_value("abc")
    at.button(key="fs_verify_run").click().run()
    assert "股票代码无效" in _all_text(at)
