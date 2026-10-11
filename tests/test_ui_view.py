"""财报展示页纯逻辑测试：本地构造数据 + monkeypatch，不依赖网络与 Streamlit 运行时。"""
import os
import time

import pandas as pd
import pytest

from finsight.datasources import em_statements
from finsight.ui.view import (
    build_display_frame,
    fetch_with_source,
    format_yuan,
    period_label,
    statement_caption,
)


# ---------------- 展示格式化 ----------------

def test_format_yuan():
    assert format_yuan(1234567.0) == "1,234,567"
    assert format_yuan(-95.4) == "-95"
    assert format_yuan(0) == "0"
    assert format_yuan(None) == "—"
    assert format_yuan(float("nan")) == "—"
    assert format_yuan(3.09e11) == "309,000,000,000"


def test_period_label():
    assert period_label(pd.Timestamp(2024, 6, 30), "中报") == "2024Q2·中报"
    # 无 REPORT_TYPE 时按季度推断
    assert period_label(pd.Timestamp(2024, 12, 31), None) == "2024Q4·年报"
    assert period_label(pd.Timestamp(2024, 3, 31)) == "2024Q1·一季报"
    # 非标准季末：退化为日期字符串，不猜测季度
    assert period_label(pd.Timestamp(2024, 5, 31)) == "2024-05-31"
    assert period_label(pd.NaT) == "—"


def test_statement_caption_value_basis():
    assert "时点值" in statement_caption("balance")
    assert "累计值" in statement_caption("income")
    assert "累计值" in statement_caption("cashflow")
    for kind in ("balance", "income", "cashflow"):
        assert "单位：元" in statement_caption(kind)


def _sample_std_balance():
    return pd.DataFrame({
        "report_date": [pd.Timestamp(2024, 3, 31), pd.Timestamp(2024, 6, 30), pd.Timestamp(2024, 9, 30)],
        "report_type": ["一季报", "中报", "三季报"],
        "total_assets": [100.0, 200.0, float("nan")],
        "total_liabilities": [40.0, float("nan"), 90.0],
        "total_current_assets": [50.0, 80.0, 110.0],
        "total_current_liabilities": [20.0, 30.0, 40.0],
        "inventory": [10.0, 20.0, 30.0],
        "accounts_receivable": [1.0, 2.0, 3.0],
        "total_equity": [60.0, 120.0, 180.0],
    })


def test_build_display_frame_latest_first_and_missing():
    display = build_display_frame(_sample_std_balance(), "balance")
    assert display.iloc[0]["报告期"] == "2024Q3·三季报"          # 倒序，最新在前
    assert display.iloc[-1]["报告期"] == "2024Q1·一季报"
    assert "总资产（元）" in display.columns                     # 中文标准字段表头
    assert display.iloc[0]["总资产（元）"] == "—"                # 缺失显式标注
    assert display.iloc[0]["总负债（元）"] == "90"
    assert display.iloc[1]["总负债（元）"] == "—"
    assert display.iloc[2]["总资产（元）"] == "100"


def test_build_display_frame_max_periods():
    display = build_display_frame(_sample_std_balance(), "balance", max_periods=2)
    assert len(display) == 2
    assert display.iloc[0]["报告期"] == "2024Q3·三季报"


def test_build_display_frame_empty():
    assert build_display_frame(pd.DataFrame(), "balance").empty


# ---------------- 数据来源标注（monkeypatch fetch_statement，不联网） ----------------

@pytest.fixture()
def cache_env(tmp_path, monkeypatch):
    """把缓存目录指向临时目录，便于构造/检查缓存文件。"""
    monkeypatch.setattr(em_statements, "CACHE_DIR", tmp_path)
    return tmp_path


def _write_cache(cache_dir, em_symbol="SH600519", kind="balance", age_hours=0.0):
    path = cache_dir / f"{em_symbol}_{kind}.csv"
    path.write_text("REPORT_DATE\n2024-06-30 00:00:00\n", encoding="utf-8-sig")
    if age_hours:
        old = time.time() - age_hours * 3600
        os.utime(path, (old, old))
    return path


def test_source_fresh_fetch_when_no_cache(cache_env, monkeypatch):
    def fake_fetch(code, kind, refresh=False):
        _write_cache(cache_env)  # 取数成功分支会写入缓存
        return pd.DataFrame({"REPORT_DATE": ["2024-06-30 00:00:00"]})
    monkeypatch.setattr(em_statements, "fetch_statement", fake_fetch)
    _, source = fetch_with_source("600519", "balance")
    assert source.startswith("本地缓存（本次新写入")  # 只陈述缓存状态


def test_source_cache_hit(cache_env, monkeypatch):
    _write_cache(cache_env, age_hours=0.5)  # 新鲜缓存

    def fake_fetch(code, kind, refresh=False):  # 缓存命中：不写文件
        return pd.DataFrame({"REPORT_DATE": ["2024-06-30 00:00:00"]})
    monkeypatch.setattr(em_statements, "fetch_statement", fake_fetch)
    _, source = fetch_with_source("600519", "balance")
    assert source.startswith("本地缓存（") and "未过期" in source
    # 不得出现对网络行为的确定性断言
    assert "未访问接口" not in source and "实时拉取" not in source


def test_source_stale_cache_fallback(cache_env, monkeypatch):
    _write_cache(cache_env, age_hours=30.0)  # 过期缓存

    def fake_fetch(code, kind, refresh=False):  # mtime 未变且已过期
        return pd.DataFrame({"REPORT_DATE": ["2024-06-30 00:00:00"]})
    monkeypatch.setattr(em_statements, "fetch_statement", fake_fetch)
    _, source = fetch_with_source("600519", "balance")
    assert source.startswith("本地过期缓存")
    assert "可能未获取成功" in source and "接口取数失败" not in source


def test_source_refresh_updates_cache(cache_env, monkeypatch):
    path = _write_cache(cache_env, age_hours=0.5)

    def fake_fetch(code, kind, refresh=False):
        assert refresh is True
        path.write_text("REPORT_DATE\n2024-09-30 00:00:00\n", encoding="utf-8-sig")  # 更新缓存
        return pd.DataFrame({"REPORT_DATE": ["2024-09-30 00:00:00"]})
    monkeypatch.setattr(em_statements, "fetch_statement", fake_fetch)
    _, source = fetch_with_source("600519", "balance", refresh=True)
    assert "本地缓存（本次已刷新" in source
