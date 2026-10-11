"""指标概览 / 趋势帧 / 信号展示层的离线测试（本地构造数据，不联网）。"""
import pandas as pd
import pytest

from finsight.finance.field_mapping import REPORT_DATE_FIELD, standardize_statement
from finsight.finance.signals import rule3_cashflow_profit_divergence
from finsight.ui.signals_view import (
    evaluate_available,
    format_metric,
    format_metric_lines,
    signal_summary_frame,
)
from finsight.ui.view import (
    build_indicator_display,
    build_trend_frames,
    format_ratio,
    format_times,
    format_yoy_with_status,
)


def qd(year, quarter):
    m, d = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}[quarter]
    return pd.Timestamp(year, m, d)


def make_std(kind, dates, cols):
    raw = pd.DataFrame({
        REPORT_DATE_FIELD: [d.strftime("%Y-%m-%d %H:%M:%S") for d in dates],
        **cols,
    })
    return standardize_statement(kind, raw)


def cum(singles):
    total, out = 0, []
    for s in singles:
        total += s
        out.append(total)
    return out


Y2324 = [qd(2023, q) for q in (1, 2, 3, 4)] + [qd(2024, q) for q in (1, 2, 3, 4)]


def sample_frames():
    """2023 单季收入 100、2024 单季 120/110/90/95（累计同比 +20%/+15%/+6.7%/+3.75%）。

    净利润 2023 单季 50；2024 单季 10/−5/−10/−15 → 累计 10/5/−5/−20：
    Q3/Q4 累计由正转负 → 由盈转亏（profit_to_loss）。
    """
    inc = make_std("income", Y2324, {
        "OPERATE_INCOME": cum([100] * 4) + cum([120, 110, 90, 95]),
        "OPERATE_COST": cum([60] * 4) + cum([70, 136 - 70, 190 - 136, 247 - 190]),
        "NETPROFIT": cum([50] * 4) + cum([10, -5, -10, -15]),
        "PARENT_NETPROFIT": cum([45] * 4) + cum([9, -4, -9, -13]),
    })
    bal = make_std("balance", Y2324, {
        "TOTAL_ASSETS": [1000] * 8, "TOTAL_LIABILITIES": [600] * 8,
        "TOTAL_CURRENT_ASSETS": [500] * 8, "TOTAL_CURRENT_LIAB": [400] * 8,
        "INVENTORY": [200 + i for i in range(8)], "ACCOUNTS_RECE": [100 + i for i in range(8)],
        "TOTAL_EQUITY": [400] * 8,
    })
    cf = make_std("cashflow", Y2324, {
        "NETCASH_OPERATE": cum([40] * 4) + cum([-10, 30, 40, 50]),
    })
    return inc, bal, cf


# ---------------- 格式化 ----------------

def test_format_yoy_with_status_all_cases():
    assert format_yoy_with_status(0.2, "normal") == "+20.0%"
    assert format_yoy_with_status(1.6, "turn_to_profit") == "扭亏为盈（+160.0%）"
    assert format_yoy_with_status(-1.3, "profit_to_loss") == "由盈转亏（-130.0%）"
    assert format_yoy_with_status(0.2, "loss_narrowing") == "亏损收窄（+20.0%）"
    assert format_yoy_with_status(-0.2, "loss_widening") == "亏损扩大（-20.0%）"
    assert format_yoy_with_status(None, "undefined_prior_zero") == "—"
    assert format_yoy_with_status(0.5, "undefined_prior_zero") == "上年同期为 0，无定义"
    assert format_yoy_with_status(None, "missing") == "—"


def test_format_ratio_and_times():
    assert format_ratio(0.4) == "40.0%"
    assert format_ratio(None) == "—"
    assert format_times(3.451) == "3.5 次"
    assert format_times(None) == "—"


def test_format_metric_by_key():
    assert format_metric("净利润(元)", 1.7e11) == "170,000,000,000"
    assert format_metric("增速差(百分点)", 30.0) == "+30.0"
    assert format_metric("营业收入同比", 0.123) == "+12.3%"
    assert format_metric("口径", "单季") == "单季"
    assert format_metric("流动比率", 1.25) == "1.25"
    assert format_metric("经营现金流/净利润", 0.475) == "0.47"  # 二进制浮点 0.475 实为 0.47499…
    assert format_metric("当期值", None) == "—"


# ---------------- 指标概览 ----------------

def test_indicator_display_values_and_status():
    inc, bal, cf = sample_frames()
    df, notes = build_indicator_display(inc, bal, cf, max_rows=4)
    assert list(df.columns)[:3] == ["报告期", "营业收入（累计，元）", "营收同比（累计）"]
    row = df.iloc[0]  # 2024Q4：累计收入 415、累计净利 −20
    assert row["报告期"] == "2024Q4·年报"
    assert row["营业收入（累计，元）"] == "415"
    assert row["营收同比（累计）"] == format_yoy_with_status(415 / 400 - 1, "normal")
    # 累计净利 −20 vs 上年 200：由盈转亏，必须显式标注，不得显示为普通 −110.0%
    assert row["净利润同比（累计）"] == "由盈转亏（-110.0%）"
    assert "由盈转亏" in row["归母同比（累计）"]
    assert row["毛利率（累计）"] == format_ratio((415 - 247) / 415)
    assert row["资产负债率（期末时点）"] == "60.0%"
    assert row["流动比率（期末时点）"] == "1.25"
    # 首年（2023）无上年同期：同比列 '—'（notes 说明）
    df_all, notes_all = build_indicator_display(inc, bal, cf)
    assert (df_all.iloc[4:]["营收同比（累计）"] == "—").all()
    assert any("营收同比" in n for n in notes_all)


def test_indicator_display_missing_balance():
    inc, _, _ = sample_frames()
    df, notes = build_indicator_display(inc, None, None)
    row = df.iloc[0]
    assert row["资产负债率（期末时点）"] == "—"
    assert row["应收账款周转率（年化，累计口径）"] == "—"
    assert any("资产负债表不可用" in n for n in notes)
    assert any("现金流量表不可用" in n for n in notes)


def test_indicator_display_zero_denominator_notes():
    # 某期营业收入为 0 → 毛利率/净利率该期不计算并记入 notes
    inc = make_std("income", [qd(2024, 1), qd(2024, 2)], {
        "OPERATE_INCOME": [0, 100], "OPERATE_COST": [10, 60], "NETPROFIT": [0, 5],
    })
    df, notes = build_indicator_display(inc, None, None)
    assert df.iloc[1]["毛利率（累计）"] == "—"   # 倒序：0 收入期为第一行
    assert any("毛利率" in n and "未计算" in n for n in notes)


def test_indicator_display_income_missing_all():
    df, notes = build_indicator_display(None, None, None)
    assert df.empty
    assert notes and "利润表不可用" in notes[0]


# ---------------- 趋势帧 ----------------

def test_trend_frames_single_quarter_not_interpolated():
    inc, bal, cf = sample_frames()
    frames = build_trend_frames(inc, cf, bal)
    flow = frames["单季流量"]
    assert flow.index.is_monotonic_increasing                    # 时间正序
    assert list(flow.columns) == ["营业收入（单季）", "净利润（单季）", "经营现金流净额（单季）"]
    assert flow.loc[qd(2024, 2), "营业收入（单季）"] == pytest.approx(110 / 1e8)  # 单季（亿元）
    # 缺失期（如无现金流量表）不插值：单独构造
    frames2 = build_trend_frames(inc, None, bal)
    assert "经营现金流净额（单季）" not in frames2["单季流量"].columns


def test_trend_frames_ratio_scaling():
    inc, bal, cf = sample_frames()
    frames = build_trend_frames(inc, cf, bal)
    profit = frames["盈利比率"]
    assert profit.loc[qd(2024, 4), "毛利率（%，累计口径）"] == pytest.approx((415 - 247) / 415 * 100)
    solvency = frames["偿债比率"]
    assert solvency.loc[qd(2023, 1), "资产负债率（%，期末时点）"] == pytest.approx(60.0)
    assert solvency.loc[qd(2023, 1), "流动比率（倍，期末时点）"] == pytest.approx(1.25)


# ---------------- 信号展示编排 ----------------

def test_evaluate_available_partial_tables():
    inc, bal, cf = sample_frames()
    # 缺资产负债表：R4 记为无法评估；R1/R2/R3 正常
    result = evaluate_available(inc, cf, None, company="T")
    assert any(s.rule_id == "R1" for s in result.signals)          # +20%→+10%→−10% 会触发
    assert not any(s.rule_id == "R4" for s in result.signals)
    assert any("R4" in n and "资产负债表不可用" in n for n in result.unevaluated)
    # 缺利润表：四条规则全部记为无法评估
    result2 = evaluate_available(None, cf, bal)
    assert result2.signals == []
    assert len([n for n in result2.unevaluated if "利润表不可用" in n]) == 4


def test_signal_summary_and_metric_lines():
    inc = make_std("income", [qd(2023, 1), qd(2023, 2)], {
        "NETPROFIT": [50, 100],
    })
    cf = make_std("cashflow", [qd(2023, 1), qd(2023, 2)], {
        "NETCASH_OPERATE": [10, 30],
    })
    signals = rule3_cashflow_profit_divergence(inc, cf).signals
    assert signals  # Q1: 50 vs 10 → 0.2 < 0.5 触发
    frame = signal_summary_frame(signals)
    assert frame.iloc[0]["级别"] == "关注"
    assert frame.iloc[0]["规则"] == "R3"
    lines = format_metric_lines(signals[0].metrics)
    assert any(l.startswith("经营现金流/净利润：") for l in lines)
    assert any(l.startswith("口径：") for l in lines)
