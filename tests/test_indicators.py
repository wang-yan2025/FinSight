"""财务指标引擎测试：全部手工构造数据或本地样例锚点，不依赖网络。

锚点值（亿元）手工核算自 tests/fixtures 样例：
- 营业收入累计：2024FY 1708.991523、2025FY 1688.381025、2025H1 893.893544、
  2026H1 907.032610、2026Q1 539.092522、2025Q1 506.009579
- 净利润累计：2024FY 893.347280、2025FY 853.103248
"""
import math
from pathlib import Path

import pandas as pd
import pytest

from finsight.finance.field_mapping import REPORT_DATE_FIELD, standardize_statement
from finsight.finance.indicators import (
    apply_indicator,
    current_ratio,
    debt_to_asset_ratio,
    gross_margin,
    inventory_turnover_series,
    net_margin,
    receivables_turnover_series,
    turnover_annualized,
    yoy_growth,
    yoy_series,
    yoy_status,
)

FIXTURES = Path(__file__).parent / "fixtures"


def qd(year, quarter):
    m, d = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}[quarter]
    return pd.Timestamp(year, m, d)


def make_std(kind, dates, cols):
    raw = pd.DataFrame({
        REPORT_DATE_FIELD: [d.strftime("%Y-%m-%d %H:%M:%S") for d in dates],
        **cols,
    })
    return standardize_statement(kind, raw)


# ---------------- 标量核心：正常 / 零分母 / 负数 / 缺失 ----------------

def test_gross_margin():
    assert gross_margin(1000, 600) == pytest.approx(0.4)
    assert gross_margin(100, 150) == pytest.approx(-0.5)   # 毛亏有效
    assert gross_margin(0, 50) is None                     # 零分母
    assert gross_margin(-100, 50) is None                  # 负收入异常
    assert gross_margin(None, 50) is None
    assert gross_margin(float("nan"), 50) is None


def test_net_margin():
    assert net_margin(80, 1000) == pytest.approx(0.08)
    assert net_margin(-30, 100) == pytest.approx(-0.3)     # 亏损期有效
    assert net_margin(50, 0) is None
    assert net_margin(None, 100) is None


def test_debt_to_asset_ratio():
    assert debt_to_asset_ratio(600, 1000) == pytest.approx(0.6)
    assert debt_to_asset_ratio(0, 1000) == 0.0
    assert debt_to_asset_ratio(600, 0) is None
    assert debt_to_asset_ratio(-5, 1000) is None           # 负负债异常
    assert debt_to_asset_ratio(600, None) is None


def test_current_ratio():
    assert current_ratio(500, 400) == pytest.approx(1.25)
    assert current_ratio(500, 0) is None
    assert current_ratio(500, -10) is None
    assert current_ratio(-5, 100) is None


def test_yoy_growth():
    assert yoy_growth(120, 100) == pytest.approx(0.2)
    assert yoy_growth(100, 100) == pytest.approx(0.0)
    assert yoy_growth(50, -50) == pytest.approx(2.0)       # 扭亏：分母取绝对值
    assert yoy_growth(-60, -50) == pytest.approx(-0.2)     # 亏损扩大
    assert yoy_growth(50, 0) is None                       # 上年为 0 无定义
    assert yoy_growth(None, 100) is None


def test_turnover_annualized():
    assert turnover_annualized(1200, 100, 140, 12) == pytest.approx(10.0)   # 年均值
    assert turnover_annualized(600, 110, 120, 6) == pytest.approx(600 * 2 / 115)   # 半年化
    assert turnover_annualized(90, 100, 110, 3) == pytest.approx(90 * 4 / 105)     # 季度年化
    assert turnover_annualized(1200, 100, -100, 12) is None  # 平均余额为 0
    assert turnover_annualized(1200, 60, -80, 12) is None    # 平均余额为负
    assert turnover_annualized(-50, 100, 140, 12) is None    # 流量为负异常
    assert turnover_annualized(1200, None, 140, 12) is None
    assert turnover_annualized(1200, 100, 140, 0) is None    # 月数非正


# ---------------- apply_indicator：逐期计算 + 缺失原因 ----------------

def test_apply_indicator_reasons():
    std = make_std("income", [qd(2024, 1), qd(2024, 2)],
                   {"OPERATE_INCOME": [1000, None], "OPERATE_COST": [600, 300]})
    out = apply_indicator(std, gross_margin,
                          {"revenue": "revenue", "operate_cost": "operate_cost"},
                          value_name="gross_margin")
    assert out.iloc[0]["gross_margin"] == pytest.approx(0.4)
    assert out.iloc[0]["reason"] == ""
    assert pd.isna(out.iloc[1]["gross_margin"])
    assert "revenue" in out.iloc[1]["reason"]


def test_apply_indicator_zero_denominator_reason():
    std = make_std("income", [qd(2024, 1)], {"OPERATE_INCOME": [0], "OPERATE_COST": [10]})
    out = apply_indicator(std, gross_margin,
                          {"revenue": "revenue", "operate_cost": "operate_cost"})
    assert pd.isna(out.iloc[0]["value"])
    assert out.iloc[0]["reason"]


def test_apply_indicator_net_profit_vs_parent_not_mixed():
    """净利率必须取净利润列，不得混用归母净利润。"""
    std = make_std("income", [qd(2024, 4)],
                   {"OPERATE_INCOME": [1000], "NETPROFIT": [90], "PARENT_NETPROFIT": [80]})
    out = apply_indicator(std, net_margin,
                          {"net_profit": "net_profit", "revenue": "revenue"})
    assert out.iloc[0]["value"] == pytest.approx(0.09)
    assert out.iloc[0]["net_profit"] == pytest.approx(90)


# ---------------- 同比：同期对齐 / 单季与累计两种口径 ----------------

DATES_2324 = [qd(2023, q) for q in (1, 2, 3, 4)] + [qd(2024, q) for q in (1, 2, 3, 4)]
REV_CUM = [100, 210, 320, 430, 110, 230, 350, 470]  # 单季 100/110/110/110 + 110/120/120/120


def test_yoy_series_cumulative_alignment():
    std = make_std("income", DATES_2324, {"OPERATE_INCOME": REV_CUM})
    out = yoy_series(std, "revenue", basis="cumulative")
    idx = out.set_index("report_date")
    # 累计同比：同期累计对同年同期累计（Q2 对 Q2，FY 对 FY）
    assert idx.loc[qd(2024, 2), "yoy"] == pytest.approx((230 - 210) / 210)
    assert idx.loc[qd(2024, 4), "yoy"] == pytest.approx((470 - 430) / 430)
    assert idx.loc[qd(2024, 1), "prior_value"] == pytest.approx(100)
    # 首年无上年同期
    assert pd.isna(idx.loc[qd(2023, 3), "yoy"])
    assert "上年同期" in idx.loc[qd(2023, 3), "reason"]


def test_yoy_series_single_quarter_alignment():
    std = make_std("income", DATES_2324, {"OPERATE_INCOME": REV_CUM})
    out = yoy_series(std, "revenue", basis="single_quarter")
    idx = out.set_index("report_date")
    # 单季同比：2024Q2 单季 120 vs 2023Q2 单季 110（而非累计 230 vs 210）
    assert idx.loc[qd(2024, 2), "current_value"] == pytest.approx(120)
    assert idx.loc[qd(2024, 2), "prior_value"] == pytest.approx(110)
    assert idx.loc[qd(2024, 2), "yoy"] == pytest.approx((120 - 110) / 110)
    assert (out["basis"] == "single_quarter").all()


def test_yoy_series_single_quarter_prior_unrestored():
    # 上年缺 Q2 累计 → 上年 Q2 单季不可还原 → 当年 Q2 单季同比缺失
    dates = [qd(2023, 1), qd(2023, 3), qd(2023, 4), qd(2024, 1), qd(2024, 2)]
    std = make_std("income", dates, {"OPERATE_INCOME": [100, 320, 430, 110, 230]})
    out = yoy_series(std, "revenue", basis="single_quarter")
    idx = out.set_index("report_date")
    assert pd.isna(idx.loc[qd(2024, 2), "yoy"])
    assert idx.loc[qd(2024, 2), "reason"]


def test_yoy_series_prior_zero_undefined():
    std = make_std("income", [qd(2023, 1), qd(2024, 1)], {"NETPROFIT": [0, 30]})
    out = yoy_series(std, "net_profit", basis="cumulative")
    assert pd.isna(out.iloc[1]["yoy"])
    assert "为 0" in out.iloc[1]["reason"]
    assert out.iloc[1]["status"] == "undefined_prior_zero"


def test_yoy_series_negative_prior_profit():
    # 扭亏：上年 -50，当年 30 → (30-(-50))/50 = 1.6，状态必须标注扭亏为盈
    std = make_std("income", [qd(2023, 4), qd(2024, 4)], {"NETPROFIT": [-50, 30]})
    out = yoy_series(std, "net_profit", basis="cumulative")
    assert out.iloc[1]["yoy"] == pytest.approx(1.6)
    assert out.iloc[1]["status"] == "turn_to_profit"


def test_yoy_series_loss_scenarios_status():
    # 亏损收窄：-50 → -40，数值 +0.2 但不是"普通增长"
    std = make_std("income", [qd(2023, 4), qd(2024, 4)], {"NETPROFIT": [-50, -40]})
    out = yoy_series(std, "net_profit", basis="cumulative")
    assert out.iloc[1]["yoy"] == pytest.approx(0.2)
    assert out.iloc[1]["status"] == "loss_narrowing"
    # 亏损扩大：-50 → -60，数值 -0.2
    std = make_std("income", [qd(2023, 4), qd(2024, 4)], {"NETPROFIT": [-50, -60]})
    out = yoy_series(std, "net_profit", basis="cumulative")
    assert out.iloc[1]["yoy"] == pytest.approx(-0.2)
    assert out.iloc[1]["status"] == "loss_widening"


def test_yoy_series_normal_status():
    std = make_std("income", DATES_2324, {"OPERATE_INCOME": REV_CUM})
    out = yoy_series(std, "revenue", basis="cumulative")
    computed = out[out["reason"] == ""]
    assert (computed["status"] == "normal").all()
    # 首年（无上年同期）→ missing
    first_year = out[out["report_date"].dt.year == 2023]
    assert (first_year["status"] == "missing").all()


def test_yoy_status_classification():
    assert yoy_status(120, 100) == "normal"
    assert yoy_status(-30, 100) == "profit_to_loss"  # 由盈转亏：不得按普通下滑解读
    assert yoy_status(50, -50) == "turn_to_profit"
    assert yoy_status(-40, -50) == "loss_narrowing"
    assert yoy_status(0, -50) == "loss_narrowing"    # 收窄至盈亏平衡
    assert yoy_status(-60, -50) == "loss_widening"
    assert yoy_status(-50, -50) == "loss_widening"   # 亏损持平归入未收窄
    assert yoy_status(50, 0) == "undefined_prior_zero"
    assert yoy_status(None, 100) == "missing"
    assert yoy_status(30, None) == "missing"
    assert yoy_status(float("nan"), 100) == "missing"


def test_yoy_status_current_zero_boundaries():
    """当期为 0 的三个边界：正基数→normal（同比-100%）；负基数→收窄；双零→无定义。"""
    assert yoy_status(0, 100) == "normal"             # 盈利归零：同比 -100%，语义清晰
    assert yoy_growth(0, 100) == pytest.approx(-1.0)  # 数值仍有效
    assert yoy_status(0, -50) == "loss_narrowing"     # 亏损收窄至盈亏平衡
    assert yoy_status(0, 0) == "undefined_prior_zero" # 双零：以上年为 0 论，无定义


def test_yoy_series_profit_to_loss():
    # 由盈转亏：上年 100，当年 -30 → (−30−100)/100 = −1.3，状态必须标注
    std = make_std("income", [qd(2023, 4), qd(2024, 4)], {"NETPROFIT": [100, -30]})
    out = yoy_series(std, "net_profit", basis="cumulative")
    assert out.iloc[1]["yoy"] == pytest.approx(-1.3)
    assert out.iloc[1]["status"] == "profit_to_loss"


def test_yoy_series_current_zero_positive_prior():
    # 盈利归零：yoy = −1.0，状态 normal（同比 −100% 至盈亏平衡，语义清晰）
    std = make_std("income", [qd(2023, 4), qd(2024, 4)], {"NETPROFIT": [100, 0]})
    out = yoy_series(std, "net_profit", basis="cumulative")
    assert out.iloc[1]["yoy"] == pytest.approx(-1.0)
    assert out.iloc[1]["status"] == "normal"


def test_yoy_series_invalid_basis():
    std = make_std("income", [qd(2024, 1)], {"OPERATE_INCOME": [100]})
    with pytest.raises(ValueError):
        yoy_series(std, "revenue", basis="mom")  # 不提供累计值环比


# ---------------- 周转率：平均余额 / 期间 / 数据不足 ----------------

BAL_DATES = [qd(2023, 4)] + [qd(2024, q) for q in (1, 2, 3, 4)]
AR_BAL = [100, 110, 120, 130, 140]
INV_BAL = [200, 210, 220, 230, 240]
INC_DATES = [qd(2024, q) for q in (1, 2, 3, 4)]
REV_CUM_24 = [90, 190, 300, 420]
COST_CUM_24 = [60, 130, 210, 300]


def test_receivables_turnover_cumulative_uses_year_start_balance():
    bal = make_std("balance", BAL_DATES, {"ACCOUNTS_RECE": AR_BAL})
    inc = make_std("income", INC_DATES, {"OPERATE_INCOME": REV_CUM_24})
    out = receivables_turnover_series(bal, inc, basis="cumulative")
    idx = out.set_index("report_date")
    # Q1：年初=上年年报 100，期末 110，avg 105，累计流量 90（3 个月 ×4）
    assert idx.loc[qd(2024, 1), "value"] == pytest.approx(90 * 4 / 105)
    assert idx.loc[qd(2024, 1), "begin_balance"] == pytest.approx(100)
    assert idx.loc[qd(2024, 1), "months"] == 3
    # H1：累计流量覆盖 1-6 月 → 平均余额必须用【年初】100 而非 Q1 末 110
    assert idx.loc[qd(2024, 2), "begin_balance"] == pytest.approx(100)
    assert idx.loc[qd(2024, 2), "value"] == pytest.approx(190 * 2 / 110)
    # FY：年初 100，期末 140，avg 120，累计流量 420
    assert idx.loc[qd(2024, 4), "value"] == pytest.approx(420 / 120)
    assert (out["reason"] == "").all()
    assert (out["basis"] == "cumulative").all()


def test_receivables_turnover_single_quarter_uses_prev_quarter_balance():
    bal = make_std("balance", BAL_DATES, {"ACCOUNTS_RECE": AR_BAL})
    inc = make_std("income", INC_DATES, {"OPERATE_INCOME": REV_CUM_24})
    out = receivables_turnover_series(bal, inc, basis="single_quarter")
    idx = out.set_index("report_date")
    # Q1 单季：流量 90，期初=上年年报 100，期末 110，avg 105
    assert idx.loc[qd(2024, 1), "value"] == pytest.approx(90 * 4 / 105)
    # Q2 单季：流量 = 190−90 = 100，期初=Q1 末 110，期末 120，avg 115
    assert idx.loc[qd(2024, 2), "flow"] == pytest.approx(100)
    assert idx.loc[qd(2024, 2), "begin_balance"] == pytest.approx(110)
    assert idx.loc[qd(2024, 2), "value"] == pytest.approx(100 * 4 / 115)
    # Q4 单季：流量 = 420−300 = 120，期初=Q3 末 130，期末 140，avg 135
    assert idx.loc[qd(2024, 4), "value"] == pytest.approx(120 * 4 / 135)
    assert (out["months"] == 3).all()
    assert (out["reason"] == "").all()


def test_inventory_turnover_uses_operate_cost():
    bal = make_std("balance", BAL_DATES, {"INVENTORY": INV_BAL})
    inc = make_std("income", INC_DATES,
                   {"OPERATE_COST": COST_CUM_24, "OPERATE_INCOME": REV_CUM_24})
    out = inventory_turnover_series(bal, inc)
    idx = out.set_index("report_date")
    # FY 累计：营业成本 300 / 平均存货（年初 200 + 期末 240）/2 = 220
    assert idx.loc[qd(2024, 4), "value"] == pytest.approx(300 / 220)
    # H1 累计：130 ×2 / 平均存货（200+220）/2 = 210
    assert idx.loc[qd(2024, 2), "value"] == pytest.approx(130 * 2 / 210)


def test_inventory_turnover_single_quarter():
    bal = make_std("balance", BAL_DATES, {"INVENTORY": INV_BAL})
    inc = make_std("income", INC_DATES, {"OPERATE_COST": COST_CUM_24})
    out = inventory_turnover_series(bal, inc, basis="single_quarter")
    idx = out.set_index("report_date")
    # Q4 单季：成本 = 300−210 = 90，平均存货（Q3 末 230 + 240）/2 = 235
    assert idx.loc[qd(2024, 4), "flow"] == pytest.approx(90)
    assert idx.loc[qd(2024, 4), "value"] == pytest.approx(90 * 4 / 235)


def test_turnover_missing_year_start_balance():
    # 无 2023 年报余额：累计口径回退用当年 Q1 余额近似（值可计算，带 caveat）；
    # 单季口径 Q1 同样回退；Q2 起用上一季末，正常
    bal = make_std("balance", BAL_DATES[1:], {"ACCOUNTS_RECE": AR_BAL[1:]})
    inc = make_std("income", INC_DATES, {"OPERATE_INCOME": REV_CUM_24})
    idx_c = receivables_turnover_series(bal, inc, basis="cumulative").set_index("report_date")
    # 回退后应有值（用 Q1 余额做期初）
    assert idx_c["value"].notna().all()
    assert idx_c["reason"].eq("").all()  # 有值，reason 清空
    assert idx_c["caveat"].str.contains("近似").all() if "caveat" in idx_c.columns else True
    idx_s = receivables_turnover_series(bal, inc, basis="single_quarter").set_index("report_date")
    # Q1 也回退有值
    assert pd.notna(idx_s.loc[qd(2024, 1), "value"])
    # Q2 单季：流量 100，期初=Q1 末 110，期末 120 → 100×4/115
    assert idx_s.loc[qd(2024, 2), "value"] == pytest.approx(100 * 4 / 115)


def test_turnover_invalid_basis():
    bal = make_std("balance", BAL_DATES, {"ACCOUNTS_RECE": AR_BAL})
    inc = make_std("income", INC_DATES, {"OPERATE_INCOME": REV_CUM_24})
    with pytest.raises(ValueError):
        receivables_turnover_series(bal, inc, basis="annualized")


def test_turnover_missing_flow_not_computed():
    bal = make_std("balance", BAL_DATES, {"ACCOUNTS_RECE": AR_BAL})
    inc = make_std("income", INC_DATES, {"OPERATE_INCOME": [90, None, 300, 420]})
    out = receivables_turnover_series(bal, inc)
    assert pd.isna(out.iloc[1]["value"])
    assert "流量缺失" in out.iloc[1]["reason"]


# ---------------- 真实样例锚点（手工核算值，见文件头注释） ----------------

@pytest.fixture(scope="module")
def fixture_income_std():
    return standardize_statement(
        "income", pd.read_csv(FIXTURES / "SH600519_income_sample.csv", encoding="utf-8-sig"))


@pytest.fixture(scope="module")
def fixture_balance_std():
    return standardize_statement(
        "balance", pd.read_csv(FIXTURES / "SH600519_balance_sample.csv", encoding="utf-8-sig"))


def test_fixture_yoy_revenue_fy2025(fixture_income_std):
    out = yoy_series(fixture_income_std, "revenue", basis="cumulative")
    idx = out.set_index("report_date")
    expected = (1688.381025 - 1708.991523) / 1708.991523
    assert idx.loc[qd(2025, 4), "yoy"] == pytest.approx(expected, rel=1e-6)


def test_fixture_yoy_net_profit_fy2025(fixture_income_std):
    out = yoy_series(fixture_income_std, "net_profit", basis="cumulative")
    idx = out.set_index("report_date")
    expected = (853.103248 - 893.347280) / 893.347280
    assert idx.loc[qd(2025, 4), "yoy"] == pytest.approx(expected, rel=1e-6)


def test_fixture_yoy_revenue_single_q2_2026(fixture_income_std):
    """2026Q2 营业收入单季同比 = (367.940088 − 387.883965)/387.883965（手工核算）。"""
    out = yoy_series(fixture_income_std, "revenue", basis="single_quarter")
    idx = out.set_index("report_date")
    assert idx.loc[qd(2026, 2), "current_value"] == pytest.approx(
        (907.032610 - 539.092522) * 1e8, rel=1e-6)
    expected = ((907.032610 - 539.092522) - (893.893544 - 506.009579)) / (893.893544 - 506.009579)
    assert idx.loc[qd(2026, 2), "yoy"] == pytest.approx(expected, rel=1e-6)


def test_fixture_turnover_smoke(fixture_balance_std, fixture_income_std):
    """样例周转率冒烟：正常行有值、reason 为空、月数正确（含单季口径）。

    茅台应收账款余额极小（先款后货），周转率数值巨大属真实数据特征，
    此处仅验证口径与管线，不断言具体倍数。
    """
    for out, months_h1 in [
        (receivables_turnover_series(fixture_balance_std, fixture_income_std), 6),
        (inventory_turnover_series(fixture_balance_std, fixture_income_std), 6),
    ]:
        idx = out.set_index("report_date")
        row = idx.loc[qd(2026, 2)]
        assert row["reason"] == ""
        assert row["months"] == months_h1
        assert pd.notna(row["value"]) and math.isfinite(row["value"])
    # 单季口径：2026Q2 期间月数 = 3
    out_sq = receivables_turnover_series(fixture_balance_std, fixture_income_std,
                                         basis="single_quarter")
    row = out_sq.set_index("report_date").loc[qd(2026, 2)]
    assert row["reason"] == ""
    assert row["months"] == 3
    assert pd.notna(row["value"]) and math.isfinite(row["value"])


def test_fixture_yoy_has_status_column(fixture_income_std):
    """真实样例上 status 列完整：每行有分类，茅台近年盈利 → normal。"""
    out = yoy_series(fixture_income_std, "net_profit", basis="cumulative")
    assert set(out["status"]) <= {"normal", "missing", "undefined_prior_zero",
                                  "turn_to_profit", "loss_narrowing", "loss_widening"}
    computed = out[out["reason"] == ""]
    assert (computed["status"] == "normal").all()
