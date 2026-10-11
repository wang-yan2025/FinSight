"""Streamlit AppTest 端到端流程验证（离线 mock + 本地缓存，不依赖实时联网）。

覆盖：完整查询流程、指标/风险信号/报表渲染、无效代码提示、
单表获取失败的降级展示、真实缓存数据驱动流程。
"""
import os
import time
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from finsight.datasources import em_statements
from finsight.finance.field_mapping import REPORT_DATE_FIELD

APP = Path(__file__).resolve().parent.parent / "app.py"
REAL_CACHE = Path(__file__).resolve().parent.parent / "data" / "cache"


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


def _run_query(monkeypatch, fail_kinds=()):
    def fake_fetch(code, kind, refresh=False):
        if kind in fail_kinds:
            raise RuntimeError("模拟接口异常")
        return RAW_BY_KIND[kind].copy()

    monkeypatch.setattr(em_statements, "fetch_statement", fake_fetch)
    at = AppTest.from_file(str(APP), default_timeout=120)
    at.run()
    at.text_input(key="fs_code").set_value("600519")
    at.button(key="fs_query").click().run()
    return at


def _all_text(at) -> str:
    parts = []
    for attr in ("warning", "caption", "info", "markdown"):
        for el in getattr(at, attr, []):
            parts.append(el.value)
    for el in getattr(at, "write", []):
        parts.append(str(el.value))
    return " ".join(parts)


def test_app_invalid_code(monkeypatch):
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    at.text_input(key="fs_code").set_value("abc")
    at.button(key="fs_query").click().run()
    assert at.error and "股票代码无效" in at.error[0].value


def test_app_full_flow_offline(monkeypatch):
    at = _run_query(monkeypatch)
    assert not at.error and not at.exception
    frames = [el.value for el in at.dataframe]
    assert len(frames) >= 5  # 指标概览 + 信号汇总 + 三大报表
    indicator = frames[0]
    assert "毛利率（累计）" in indicator.columns
    assert "由盈转亏（-110.0%）" in indicator["净利润同比（累计）"].tolist()
    # 信号汇总表：R1（+20%→+10% 降幅触发）出现，规则号合法
    signal_frames = [f for f in frames if not f.empty and "触发条件" in f.columns]
    assert signal_frames
    assert "R1" in set(signal_frames[0]["规则"])
    assert set(signal_frames[0]["规则"]) <= {"R1", "R2", "R3", "R4"}
    # 风险提示免责声明
    assert "未触发信号不代表" in _all_text(at)


def test_app_partial_failure_degrades(monkeypatch):
    at = _run_query(monkeypatch, fail_kinds=("balance",))
    assert not at.exception  # 页面不崩溃
    assert "资产负债表" in _all_text(at) and "获取失败" in _all_text(at)
    frames = [el.value for el in at.dataframe]
    indicator = frames[0]
    assert (indicator["资产负债率（期末时点）"] == "—").all()  # 偿债列显式缺失
    assert len(frames) >= 4  # 指标 + 信号 + 两张报表仍在
    # R4 因资产负债表不可用记为无法评估（与未触发区分）
    assert "R4" in _all_text(at) and "无法评估" in _all_text(at)


@pytest.mark.skipif(
    not all((REAL_CACHE / f"SH600519_{k}.csv").exists()
            for k in ("income", "balance", "cashflow")),
    reason="本地无 600519 三表缓存",
)
def test_app_real_cached_data(monkeypatch, tmp_path):
    """真实 600519 本地缓存驱动的完整流程（缓存命中分支，不联网）。"""
    for kind in ("income", "balance", "cashflow"):
        name = f"SH600519_{kind}.csv"
        (tmp_path / name).write_bytes((REAL_CACHE / name).read_bytes())
    old = time.time() - 600  # 10 分钟前 → 新鲜缓存，走缓存命中分支
    for kind in ("income", "balance", "cashflow"):
        os.utime(tmp_path / f"SH600519_{kind}.csv", (old, old))
    monkeypatch.setattr(em_statements, "CACHE_DIR", tmp_path)

    at = AppTest.from_file(str(APP), default_timeout=180)
    at.run()
    at.text_input(key="fs_code").set_value("600519")
    at.button(key="fs_query").click().run()
    assert not at.error and not at.exception
    frames = [el.value for el in at.dataframe]
    assert len(frames) >= 5
    assert "2026Q2·中报" in frames[0]["报告期"].tolist()
    assert "本地缓存（" in _all_text(at) and "未过期" in _all_text(at)
