"""单季度还原测试：全部手工构造数据或本地样例，不依赖网络。"""
from pathlib import Path

import pandas as pd
import pytest

from finsight.finance.field_mapping import REPORT_DATE_FIELD, standardize_statement
from finsight.finance.quarterly import derive_single_quarters, period_of, quarter_months

FIXTURES = Path(__file__).parent / "fixtures"


def qd(year: int, quarter: int) -> pd.Timestamp:
    """季度末日期：qd(2024, 2) -> 2024-06-30。"""
    month_day = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
    m, d = month_day[quarter]
    return pd.Timestamp(year, m, d)


def make_std(kind: str, dates: list, cols: dict) -> pd.DataFrame:
    """手工构造 EM 原始格式报表并走 standardize_statement（复用真实链路）。"""
    raw = pd.DataFrame({
        REPORT_DATE_FIELD: [d.strftime("%Y-%m-%d %H:%M:%S") for d in dates],
        **cols,
    })
    return standardize_statement(kind, raw)


# 手工设定：两年四季度累计值与预期单季值
DATES_2324 = [qd(2023, q) for q in (1, 2, 3, 4)] + [qd(2024, q) for q in (1, 2, 3, 4)]
REV_CUM = [100, 210, 320, 430, 110, 230, 350, 470]
REV_SQ_EXPECT = [100, 110, 110, 110, 110, 120, 120, 120]


def test_period_of():
    assert period_of(pd.Timestamp(2024, 3, 31)) == (2024, 1)
    assert period_of(pd.Timestamp(2024, 6, 30)) == (2024, 2)
    assert period_of(pd.Timestamp(2024, 12, 31)) == (2024, 4)
    assert period_of(pd.Timestamp(2024, 5, 31)) is None  # 非标准季末
    assert period_of(pd.Timestamp(2024, 2, 29)) is None
    assert period_of(pd.NaT) is None


def test_quarter_months():
    assert [quarter_months(q) for q in (1, 2, 3, 4)] == [3, 6, 9, 12]
    with pytest.raises(ValueError):
        quarter_months(5)


def test_derive_normal_two_years():
    std = make_std("income", DATES_2324, {"OPERATE_INCOME": REV_CUM})
    result = derive_single_quarters(std, ["revenue"])
    assert result.notes == []
    assert result.frame["revenue__sq"].tolist() == pytest.approx(REV_SQ_EXPECT)


def test_derive_q1_uses_cumulative_directly():
    std = make_std("income", [qd(2025, 1)], {"OPERATE_INCOME": [120]})
    result = derive_single_quarters(std, ["revenue"])
    assert result.frame["revenue__sq"].tolist() == [120.0]
    assert result.notes == []


def test_derive_missing_previous_quarter():
    # 2023 年缺 Q3：Q4 无法还原，Q2 正常
    dates = [qd(2023, 1), qd(2023, 2), qd(2023, 4)]
    std = make_std("income", dates, {"OPERATE_INCOME": [100, 210, 430]})
    result = derive_single_quarters(std, ["revenue"])
    values = result.frame.set_index("report_date")["revenue__sq"]
    assert values[qd(2023, 2)] == pytest.approx(110)
    assert pd.isna(values[qd(2023, 4)])
    assert any("缺少 2023Q3 累计值" in n for n in result.notes)


def test_derive_missing_current_cumulative():
    # Q2 累计值缺失：Q2 与 Q3 均无法还原，Q4 = Q4累计 - Q3累计 正常
    std = make_std("income", DATES_2324[:4],
                   {"OPERATE_INCOME": [100, None, 320, 430]})
    result = derive_single_quarters(std, ["revenue"])
    values = result.frame.set_index("report_date")["revenue__sq"]
    assert pd.isna(values[qd(2023, 2)])
    assert pd.isna(values[qd(2023, 3)])
    assert values[qd(2023, 4)] == pytest.approx(110)
    assert any("2023Q2 当期累计值缺失" in n for n in result.notes)
    assert any("缺少 2023Q2 累计值" in n for n in result.notes)


def test_derive_negative_single_quarter_is_valid():
    # 经营现金流累计回落（Q2 单季为负）：合法，不得标记为错误
    std = make_std("cashflow", [qd(2024, 1), qd(2024, 2)],
                   {"NETCASH_OPERATE": [100, 80]})
    result = derive_single_quarters(std, ["operating_cash_flow"])
    values = result.frame.set_index("report_date")["operating_cash_flow__sq"]
    assert values[qd(2024, 2)] == pytest.approx(-20)
    assert result.notes == []  # 非单调不是错误


def test_derive_non_standard_period_note():
    std = make_std("income", [pd.Timestamp(2024, 5, 31)], {"OPERATE_INCOME": [50]})
    result = derive_single_quarters(std, ["revenue"])
    assert pd.isna(result.frame["revenue__sq"]).all()
    assert any("非标准季末" in n for n in result.notes)


def test_derive_unknown_column_raises():
    std = make_std("income", [qd(2024, 1)], {"OPERATE_INCOME": [100]})
    with pytest.raises(KeyError):
        derive_single_quarters(std, ["no_such_col"])


def test_derive_on_fixture_income():
    """样例数据锚点：2026Q2 营业收入单季 = 907.032610 − 539.092522 亿（手工核算）。"""
    std = standardize_statement(
        "income", pd.read_csv(FIXTURES / "SH600519_income_sample.csv", encoding="utf-8-sig"))
    result = derive_single_quarters(std, ["revenue", "net_profit"])
    values = result.frame.set_index("report_date")
    assert values.loc[qd(2026, 2), "revenue__sq"] == pytest.approx(
        (907.032610 - 539.092522) * 1e8, rel=1e-6)
    # 样例首期为 2023 年报（Q4）：缺少 2023Q3 累计，单季应为 NaN 并给出说明
    assert pd.isna(values.loc[qd(2023, 4), "revenue__sq"])
    assert any("缺少 2023Q3 累计值" in n for n in result.notes)
    # 2024Q1（样例内首个 Q1）：单季 = 累计
    assert values.loc[qd(2024, 1), "revenue__sq"] == pytest.approx(457.755170 * 1e8, rel=1e-6)
