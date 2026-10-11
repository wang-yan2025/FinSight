"""备忘录页面 AppTest：离线 mock 取数，覆盖生成流程与状态切换。"""
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from finsight.datasources import em_statements
from finsight.finance.field_mapping import REPORT_DATE_FIELD

APP = Path(__file__).resolve().parent.parent / "app.py"


def qd(year, quarter):
    m, d = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}[quarter]
    return pd.Timestamp(year, m, d)


def cum(singles):
    total, out = 0, []
    for s in singles:
        total += s
        out.append(total)
    return out


Y2324 = [qd(2023, q) for q in (1, 2, 3, 4)] + [qd(2024, q) for q in (1, 2, 3, 4)]
DATES_STR = [d.strftime("%Y-%m-%d %H:%M:%S") for d in Y2324]

RAW_BY_KIND = {
    "income": pd.DataFrame({
        REPORT_DATE_FIELD: DATES_STR,
        "OPERATE_INCOME": cum([100] * 4) + cum([120, 110, 90, 95]),
        "OPERATE_COST": cum([60] * 4) + cum([70, 66, 54, 57]),
        "NETPROFIT": cum([50] * 4) + cum([10, -5, -10, -15]),
        "PARENT_NETPROFIT": cum([45] * 4) + cum([9, -4, -9, -13]),
    }),
    "balance": pd.DataFrame({
        REPORT_DATE_FIELD: DATES_STR,
        "TOTAL_ASSETS": [1000] * 8, "TOTAL_LIABILITIES": [600] * 8,
        "TOTAL_CURRENT_ASSETS": [500] * 8, "TOTAL_CURRENT_LIAB": [400] * 8,
        "INVENTORY": [200 + i for i in range(8)],
        "ACCOUNTS_RECE": [100, 100, 100, 100, 110, 150, 120, 130],
        "TOTAL_EQUITY": [400] * 8,
    }),
    "cashflow": pd.DataFrame({
        REPORT_DATE_FIELD: DATES_STR,
        "NETCASH_OPERATE": cum([40] * 4) + cum([-10, 30, 40, 50]),
    }),
}

MEMO_PAGE = "买方备忘录（方向6）"


@pytest.fixture(autouse=True)
def _no_llm_env(monkeypatch):
    for var in ("FINSIGHT_LLM_API_KEY", "FINSIGHT_LLM_BASE_URL", "FINSIGHT_LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture()
def offline_fetch(monkeypatch):
    def fake_fetch(code, kind, refresh=False):
        return RAW_BY_KIND[kind].copy()
    monkeypatch.setattr(em_statements, "fetch_statement", fake_fetch)


def _at(offline_fetch):
    at = AppTest.from_file(str(APP), default_timeout=120)
    at.run()
    at.radio[0].set_value(MEMO_PAGE)
    at.run()
    return at


def _all_text(at) -> str:
    parts = []
    for attr in ("warning", "caption", "info", "markdown", "write", "error"):
        for el in getattr(at, attr, []):
            parts.append(str(el.value))
    for el in getattr(at, "expander", []):  # Expander 的标题在 label 属性
        parts.append(str(getattr(el, "label", "")))
    for el in getattr(at, "exception", []):
        parts.append(str(el.value))
    return " ".join(parts)


def test_memo_generate_basic_flow(offline_fetch):
    at = _at(offline_fetch)
    at.text_input(key="fs_memo_code").set_value("600519")
    at.button(key="fs_memo_load").click().run()
    assert "已加载" in _all_text(at)
    # 默认最新报告期（2024Q4）；生成基础备忘录（LLM 未配置）
    at.button(key="fs_memo_generate").click().run()
    text = _all_text(at)
    assert "基础备忘录已生成" in text
    assert "待配置 LLM" in text
    for section in ("研究摘要", "财务表现", "关键风险", "待核实问题", "证据清单"):
        assert section in text
    # 真实财务数字与证据 ID 进入页面
    assert "415" in text and "E-REV-2024Q4" in text
    # 特殊同比状态不被误表述
    assert "由盈转亏" in text
    # 无 LLM 时不得出现伪造的 AI 分析文本
    assert "LLM 辅助生成" not in text


def test_memo_invalid_code(offline_fetch):
    at = _at(offline_fetch)
    at.text_input(key="fs_memo_code").set_value("abc")
    at.button(key="fs_memo_load").click().run()
    assert "股票代码无效" in _all_text(at)


def test_memo_switch_stock_no_stale_memo(offline_fetch):
    at = _at(offline_fetch)
    at.text_input(key="fs_memo_code").set_value("600519")
    at.button(key="fs_memo_load").click().run()
    at.button(key="fs_memo_generate").click().run()
    assert "研究摘要" in _all_text(at)
    # 切换股票：重新加载数据 → 旧备忘录清空，不残留
    at.text_input(key="fs_memo_code").set_value("000001")
    at.button(key="fs_memo_load").click().run()
    text = _all_text(at)
    assert "SZ000001" in text
    assert "研究摘要" not in text
    assert "生成备忘录" in text  # 回到待生成状态


def test_memo_partial_table_degrades(offline_fetch, monkeypatch):
    def fail_balance(code, kind, refresh=False):
        if kind == "balance":
            raise RuntimeError("模拟接口异常")
        return RAW_BY_KIND[kind].copy()
    monkeypatch.setattr(em_statements, "fetch_statement", fail_balance)
    at = _at(offline_fetch)
    at.text_input(key="fs_memo_code").set_value("600519")
    at.button(key="fs_memo_load").click().run()
    assert "资产负债表" in _all_text(at) and "获取失败" in _all_text(at)
    at.button(key="fs_memo_generate").click().run()
    text = _all_text(at)
    assert "基础备忘录已生成" in text          # 备忘录仍可生成
    assert "R4" in text and "无法评估" in text  # R4 降级说明进入待核实


def test_memo_llm_failure_keeps_deterministic(offline_fetch, monkeypatch):
    """LLM 已配置但调用失败：确定性备忘录完整保留，明确提示失败，无伪造文本。"""
    from finsight.memo.llm import LLMError

    class _FakeCfg:
        model = "test-model"

        @classmethod
        def from_env(cls):
            return cls()

        def configured(self):
            return True

    def boom(cfg, memo, focus_question=""):
        raise LLMError("接口返回 HTTP 503")

    monkeypatch.setattr("finsight.ui.memo_page.LLMConfig", _FakeCfg)
    monkeypatch.setattr("finsight.ui.memo_page.generate_llm_sections", boom)
    at = _at(offline_fetch)
    at.text_input(key="fs_memo_code").set_value("600519")
    at.button(key="fs_memo_load").click().run()
    at.button(key="fs_memo_generate").click().run()
    text = _all_text(at)
    assert "基础备忘录已生成" in text
    assert "调用失败" in text and "HTTP 503" in text
    assert "研究摘要" in text and "证据清单" in text   # 确定性内容完整
    assert "LLM 辅助生成" not in text                  # 无伪造 AI 文本
