"""风险信号规则引擎测试：全部手工构造数据，不依赖网络。

手工数值说明：各用例的累计值均由目标单季值逐季累加构造，
同比预期值在断言处以独立算式给出（非调用引擎反推）。
"""
import pandas as pd
import pytest

from finsight.finance.field_mapping import REPORT_DATE_FIELD, standardize_statement
from finsight.finance.signals import (
    evaluate_all,
    rule1_revenue_slowdown,
    rule2_revenue_up_profit_down,
    rule3_cashflow_profit_divergence,
    rule4_receivables_growth,
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


def cum_from_singles(singles):
    """[100, 110, 105, 95] -> [100, 210, 315, 410]（年初至期末累计）。"""
    total, out = 0, []
    for s in singles:
        total += s
        out.append(total)
    return out


Y2324 = [qd(2023, q) for q in (1, 2, 3, 4)] + [qd(2024, q) for q in (1, 2, 3, 4)]
Y24 = [qd(2024, q) for q in (1, 2, 3, 4)]


# ---------------- R1 收入增速放缓 ----------------

def income_frame(rev_singles_2023, rev_singles_2024, **extra_cols):
    cols = {"OPERATE_INCOME": cum_from_singles(rev_singles_2023) + cum_from_singles(rev_singles_2024)}
    cols.update(extra_cols)
    return make_std("income", Y2324, cols)


def test_r1_triggers_on_drop_and_negative():
    # 2023 单季各 100；2024 单季 120/120/105/95 → 同比 +20%/+20%/+5%/−5%
    std = income_frame([100, 100, 100, 100], [120, 120, 105, 95])
    result = rule1_revenue_slowdown(std, company="TEST")
    assert [s.period for s in result.signals] == ["2024Q3", "2024Q4"]
    s3, s4 = result.signals
    # Q3：+20% → +5%，下降 15 个百分点（阈值 10）
    assert "15.0 个百分点" in s3.condition
    assert s3.metrics["当期营业收入同比"] == pytest.approx(0.05)
    assert s3.metrics["上一可比期间同比"] == pytest.approx(0.20)
    assert s3.metrics["口径"] == "单季"
    assert s3.level == "medium"
    # Q4：+5% → −5%，同时满足降幅 10pp 与转负
    assert "负增长" in s4.condition
    assert any("转负" in c for c in s4.caveats)


def test_r1_no_trigger_on_stable_growth():
    # 各季同比稳定 +10%（2023 单季 100，2024 单季 110）
    std = income_frame([100, 100, 100, 100], [110, 110, 110, 110])
    result = rule1_revenue_slowdown(std)
    assert result.signals == []


def test_r1_unevaluated_notes_for_missing_periods():
    std = income_frame([100, 100, 100, 100], [120, 120, 105, 95])
    result = rule1_revenue_slowdown(std)
    # 首年各期：上年数据缺失，同比不可得
    assert any("2023Q3" in n and "不可得" in n for n in result.unevaluated)
    # 2024Q1：上一可比期间（上年 Q4）同比不可得，无法算增速变动
    assert any("2024Q1" in n and "上一可比期间" in n for n in result.unevaluated)


def test_r1_positive_to_negative_labels_as_turn():
    # 2024Q3：上季 +30% → 本季 −10%，真正由正转负
    std = income_frame([100, 100, 100, 100], [120, 130, 90, 95])
    sigs = {s.period: s for s in rule1_revenue_slowdown(std).signals}
    s = sigs["2024Q3"]
    assert "转为负增长" in s.condition
    assert any("转负" in c for c in s.caveats)


def test_r1_persistent_negative_not_triggered_when_improving():
    # 2024 单季 95/97/96/84 → 同比 −5%/−3%/−4%/−16%（均为持续负增长区间）
    std = income_frame([100, 100, 100, 100], [95, 97, 96, 84])
    result = rule1_revenue_slowdown(std)
    sigs = {s.period: s for s in result.signals}
    # Q2：−5% → −3%（持续负增长但改善）→ 已评估未触发：
    # 不产生信号，也不得记入 unevaluated
    assert "2024Q2" not in sigs
    assert not any("2024Q2" in n for n in result.unevaluated)
    # Q3：−3% → −4%（持续负增长且恶化 1pp，不足阈值）→ 同上，不触发不记录
    assert "2024Q3" not in sigs
    assert not any("2024Q3" in n for n in result.unevaluated)
    # Q4：−4% → −16%（持续负增长且恶化 12pp ≥ 阈值）→ 按降幅条件触发
    s4 = sigs["2024Q4"]
    assert "12.0 个百分点" in s4.condition
    assert "转为负增长" not in s4.condition  # 非转负，不得使用"转为"措辞
    assert any("持续负增长" in c and "降幅条件触发" in c for c in s4.caveats)


def test_r1_drop_boundary_exact_20_to_10():
    # +20% → +10%：理论恰降 10 个百分点（浮点上 0.0999…87 < 0.1），
    # 必须凭严格绝对容差触发，不得漏判
    std = income_frame([100, 100, 100, 100], [120, 110, 110, 110])
    result = rule1_revenue_slowdown(std)
    sigs = {s.period: s for s in result.signals}
    assert "2024Q2" in sigs
    assert "10.0 个百分点" in sigs["2024Q2"].condition
    assert sigs["2024Q2"].metrics["当期营业收入同比"] == pytest.approx(0.10)


def test_r1_zero_prior_turn_wording():
    # 上一可比期间同比恰为 0（2024Q1 同比 0%），本期 −5% → 非负转负；
    # caveat 必须称"恰为零（非负）"，不得称"为正增长"
    std = income_frame([100, 100, 100, 100], [100, 95, 90, 85])
    sigs = {s.period: s for s in rule1_revenue_slowdown(std).signals}
    s = sigs["2024Q2"]
    assert "转为负增长" in s.condition
    assert any("恰为零（非负）" in c for c in s.caveats)
    assert not any("为正增长" in c for c in s.caveats)


def test_r1_negative_with_unknown_prior_not_triggered():
    # 2024Q1 同比 −5%，上一可比期间（2023Q4）同比不可得 → 无法确认转负，不触发
    std = income_frame([100, 100, 100, 100], [95, 90, 85, 80])
    result = rule1_revenue_slowdown(std)
    assert not any(s.period == "2024Q1" for s in result.signals)
    assert any("2024Q1" in n and "无法判定" in n for n in result.unevaluated)


def test_r1_drop_reaches_threshold_without_negative():
    # 2024 单季 125/115：同比 +25% → +15%，下降恰为 10 个百分点（阈值边界）
    std = income_frame([100, 100, 100, 100], [125, 115, 110, 110])
    sigs = {s.period: s for s in rule1_revenue_slowdown(std).signals}
    s = sigs["2024Q2"]
    assert "10.0 个百分点" in s.condition
    assert "负增长" not in s.condition  # 仍为正增长，仅按降幅触发


def test_r1_falls_back_to_cumulative_with_caveat():
    # 2023 缺 Q2 → 2023Q2/Q3 单季不可还原 → 2024Q3/Q4 单季同比缺失（prev 侧）；
    # 2024Q4 与上一可比期间（2024Q3）在【累计口径】双侧可得：+33.3% → +15.0%，
    # 降幅 18.3pp ≥ 阈值 → 整对回退累计口径触发（不与单季混比）
    dates = [qd(2023, 1), qd(2023, 3), qd(2023, 4)] + Y24
    raw = {"OPERATE_INCOME": [100, 300, 400] + cum_from_singles([120, 130, 150, 60])}
    std = make_std("income", dates, raw)
    result = rule1_revenue_slowdown(std)
    sig = next(s for s in result.signals if s.period == "2024Q4")
    assert sig.metrics["口径"] == "累计（回退）"
    assert sig.metrics["当期营业收入同比"] == pytest.approx(460 / 400 - 1)
    assert sig.metrics["上一可比期间同比"] == pytest.approx(400 / 300 - 1)
    assert sig.metrics["增速变动(百分点)"] == pytest.approx((400 / 300 - 1 - (460 / 400 - 1)) * 100, abs=1e-6)
    assert any("回退" in c for c in sig.caveats)


# ---------------- R2 收入增长、利润下滑 ----------------

def test_r2_triggered_by_parent_profit_only():
    # 单季：rev 100→110（+10%），np 50→51（+2%），pnp 45→41.4（−8%）
    std = income_frame(
        [100, 100, 100, 100], [110, 110, 110, 110],
        NETPROFIT=cum_from_singles([50, 50, 50, 50]) + cum_from_singles([51, 51, 51, 51]),
        PARENT_NETPROFIT=cum_from_singles([45, 45, 45, 45]) + cum_from_singles([41.4, 41.4, 41.4, 41.4]),
    )
    result = rule2_revenue_up_profit_down(std, company="TEST")
    periods = {s.period for s in result.signals}
    assert periods == {"2024Q1", "2024Q2", "2024Q3", "2024Q4"}
    s = next(s for s in result.signals if s.period == "2024Q2")
    assert s.metrics["口径"] == "单季"
    assert s.metrics["净利润同比"] == pytest.approx(0.02)      # 未触发口径
    assert s.metrics["归母净利润同比"] == pytest.approx(-0.08)  # 触发口径
    assert "归母净利润" in s.condition
    assert s.level == "medium"


def test_r2_triggered_by_net_profit_only():
    # np 50→47（−6%）触发，pnp 45→46.8（+4%）未触发：两口径分别标注
    std = income_frame(
        [100, 100, 100, 100], [110, 110, 110, 110],
        NETPROFIT=cum_from_singles([50, 50, 50, 50]) + cum_from_singles([47, 47, 47, 47]),
        PARENT_NETPROFIT=cum_from_singles([45, 45, 45, 45]) + cum_from_singles([46.8, 46.8, 46.8, 46.8]),
    )
    s = next(s for s in rule2_revenue_up_profit_down(std).signals if s.period == "2024Q2")
    assert s.metrics["净利润同比"] == pytest.approx(-0.06)
    assert s.metrics["归母净利润同比"] == pytest.approx(0.04)
    assert "净利润同比下滑" in s.condition and "归母" not in s.condition


def test_r2_not_triggered_when_all_positive():
    std = income_frame(
        [100, 100, 100, 100], [110, 110, 110, 110],
        NETPROFIT=cum_from_singles([50, 50, 50, 50]) + cum_from_singles([52.5, 52.5, 52.5, 52.5]),
        PARENT_NETPROFIT=cum_from_singles([45, 45, 45, 45]) + cum_from_singles([46.8, 46.8, 46.8, 46.8]),
    )
    result = rule2_revenue_up_profit_down(std)
    assert result.signals == []


def test_r2_requires_positive_revenue_growth():
    # 收入下滑（−5%）时即便利润下滑也不触发（规则前提：收入正增长）
    std = income_frame(
        [100, 100, 100, 100], [95, 95, 95, 95],
        NETPROFIT=cum_from_singles([50, 50, 50, 50]) + cum_from_singles([40, 40, 40, 40]),
        PARENT_NETPROFIT=cum_from_singles([45, 45, 45, 45]) + cum_from_singles([40, 40, 40, 40]),
    )
    assert rule2_revenue_up_profit_down(std).signals == []


# ---------------- R3 经营现金流与净利润背离 ----------------

def r3_frames(np_singles, ocf_singles):
    inc = make_std("income", Y24, {"NETPROFIT": cum_from_singles(np_singles)})
    cf = make_std("cashflow", Y24, {"NETCASH_OPERATE": cum_from_singles(ocf_singles)})
    return inc, cf


def test_r3_high_level_when_ocf_negative():
    inc, cf = r3_frames([100, 100, 100, 100], [-20, 40, 80, 90])
    result = rule3_cashflow_profit_divergence(inc, cf, company="TEST")
    assert [(s.period, s.level) for s in result.signals] == [("2024Q1", "high"), ("2024Q2", "medium")]
    high, medium = result.signals
    assert high.metrics["经营现金流/净利润"] == pytest.approx(-0.2)
    assert "为负" in high.condition
    assert medium.metrics["经营现金流/净利润"] == pytest.approx(0.4)  # < 0.5 阈值
    assert high.metrics["口径"] == "单季"
    # 健康期间（0.8、0.9）不触发
    assert not any(s.period in ("2024Q3", "2024Q4") for s in result.signals)


def test_r3_not_triggered_when_ratio_healthy():
    inc, cf = r3_frames([100, 100, 100, 100], [80, 80, 80, 80])
    assert rule3_cashflow_profit_divergence(inc, cf).signals == []


def test_r3_nonpositive_profit_not_evaluated():
    # 2025Q1 净利润 −50：分母非正，不计算比率，明确记录原因
    inc = make_std("income", Y24 + [qd(2025, 1)],
                   {"NETPROFIT": cum_from_singles([100, 100, 100, 100]) + [-50]})
    cf = make_std("cashflow", Y24 + [qd(2025, 1)],
                  {"NETCASH_OPERATE": cum_from_singles([80, 80, 80, 80]) + [30]})
    result = rule3_cashflow_profit_divergence(inc, cf)
    assert not any(s.period == "2025Q1" for s in result.signals)
    assert any("2025Q1" in n and "非正" in n for n in result.unevaluated)


def test_r3_missing_cashflow_fallback_and_note():
    # 现金流缺 2024Q3 累计：Q3 双口径不齐备 → 未评估；Q4 单季缺 → 回退累计 190/400 = 0.475
    inc = make_std("income", Y24, {"NETPROFIT": cum_from_singles([100, 100, 100, 100])})
    cf = make_std("cashflow", [qd(2024, 1), qd(2024, 2), qd(2024, 4)],
                  {"NETCASH_OPERATE": [-20, 20, 190]})
    result = rule3_cashflow_profit_divergence(inc, cf)
    assert any("2024Q3" in n and "不齐备" in n for n in result.unevaluated)
    s = next(s for s in result.signals if s.period == "2024Q4")
    assert s.metrics["口径"] == "累计（回退）"
    assert s.metrics["经营现金流/净利润"] == pytest.approx(0.475)
    assert any("回退" in c for c in s.caveats)


# ---------------- R4 应收账款增长过快 ----------------

def r4_frames(ar_2023, ar_2024, rev_cum_2023, rev_cum_2024, bal_dates=None, inc_dates=None):
    bal = make_std("balance",
                   bal_dates or [qd(2023, q) for q in (1, 2, 3, 4)] + Y24,
                   {"ACCOUNTS_RECE": ar_2023 + ar_2024})
    inc = make_std("income", inc_dates or Y2324,
                   {"OPERATE_INCOME": rev_cum_2023 + rev_cum_2024})
    return bal, inc


def test_r4_triggered_on_gap():
    # 2024Q2：AR +50% vs 营收 +20% → 差 30pp（阈值 20）；其余季度差 < 20pp
    bal, inc = r4_frames([100, 100, 100, 100], [110, 150, 120, 130],
                         [100, 200, 300, 400], [120, 240, 360, 480])
    result = rule4_receivables_growth(inc, bal, company="TEST")
    assert [s.period for s in result.signals] == ["2024Q2"]
    s = result.signals[0]
    assert s.metrics["应收账款同比"] == pytest.approx(0.5)
    assert s.metrics["营业收入同比（累计口径）"] == pytest.approx(0.2)
    assert s.metrics["增速差(百分点)"] == pytest.approx(30.0)
    assert s.level == "medium"
    # 2023 各期无上年余额 → 明确记录
    assert any("2023Q2" in n and "缺失" in n for n in result.unevaluated)


def test_r4_not_triggered_on_small_gap():
    bal, inc = r4_frames([100, 100, 100, 100], [110, 120, 120, 130],
                         [100, 200, 300, 400], [120, 240, 360, 480])
    assert rule4_receivables_growth(inc, bal).signals == []


def test_r4_zero_prior_balance_undefined():
    bal, inc = r4_frames([100, 0, 100, 100], [110, 150, 120, 130],
                         [100, 200, 300, 400], [120, 240, 360, 480])
    result = rule4_receivables_growth(inc, bal)
    assert not any(s.period == "2024Q2" for s in result.signals)
    assert any("2024Q2" in n and "为 0" in n and "无定义" in n for n in result.unevaluated)


def test_r4_period_misalignment_note():
    # 利润表缺 2024Q3：该期营收同比不可得，明确记录而非误算
    bal, inc = r4_frames([100, 100, 100, 100], [110, 150, 150, 130],
                         [100, 200, 300, 400], [120, 240, 480],
                         inc_dates=[qd(2023, q) for q in (1, 2, 3, 4)]
                         + [qd(2024, 1), qd(2024, 2), qd(2024, 4)])
    result = rule4_receivables_growth(inc, bal)
    assert any("2024Q3" in n and "营业收入同比不可得" in n for n in result.unevaluated)
    assert not any(s.period == "2024Q3" for s in result.signals)


# ---------------- 汇总与信号完整性 ----------------

def test_evaluate_all_aggregates_and_fields_complete():
    bal, inc = r4_frames([100, 100, 100, 100], [110, 150, 120, 130],
                         [100, 200, 300, 400], [120, 240, 360, 480])
    inc_full = make_std("income", Y2324, {
        "OPERATE_INCOME": [100, 200, 300, 400] + cum_from_singles([110, 110, 110, 110]),
        "NETPROFIT": cum_from_singles([50, 50, 50, 50]) + cum_from_singles([51, 51, 51, 51]),
        "PARENT_NETPROFIT": cum_from_singles([45, 45, 45, 45]) + cum_from_singles([41.4, 41.4, 41.4, 41.4]),
    })
    cf = make_std("cashflow", Y24, {"NETCASH_OPERATE": cum_from_singles([-20, 40, 80, 90])})
    result = evaluate_all(inc_full, cf, bal, company="SHTEST")
    assert {s.rule_id for s in result.signals} >= {"R2", "R3", "R4"}
    for s in result.signals:
        assert s.kind == "risk_hint"
        assert s.company == "SHTEST" and s.period and s.condition
        assert s.metrics and s.fields_used and "数据来源" in s.fields_used
        assert s.suggestions and s.interpretation
        assert not pd.isna(s.report_date)
    # 未评估原因一并汇总
    assert result.unevaluated


def test_signals_are_risk_hints_not_assertions():
    """措辞约束：全部信号为风险提示，不得出现断言式结论。"""
    inc, cf = r3_frames([100, 100, 100, 100], [-20, 40, 80, 90])
    for s in rule3_cashflow_profit_divergence(inc, cf).signals:
        assert s.kind == "risk_hint"
        assert "不构成异常结论" in s.interpretation
        assert any("核查" in x for x in s.suggestions)
