"""核验规则测试：固定手算数据集 + 独立预期值，不与实现共用公式。

参考数据（元）：2023 单季收入/净利 100/50 ×4；2024 单季收入 120/110/90/95
→ 累计 120/230/320/415；净利 10/−5/−10/−15 → 累计 10/5/−5/−20；
毛利率 FY = (415−247)/415 ≈ 40.48%；收入同比 FY = 3.75%、H1 = 15%；
净利同比 FY = −110%（由盈转亏）；总资产 1000、总负债 600、
资产负债率 60%、流动比率 1.25；AR 2024Q4 = 130。
"""
import pandas as pd
import pytest

from finsight.finance.field_mapping import REPORT_DATE_FIELD, standardize_statement
from finsight.verify.claims import Claim
from finsight.verify.reference import build_reference_snapshot
from finsight.verify.rules import (
    check_source_descriptions,
    verify_claim_set,
    verify_numeric,
    verify_qualitative,
    verify_status_word,
)

Y2324_DATES = [f"20{23 + i // 4}-{m:02d}-{d:02d} 00:00:00"
               for i in range(8) for m, d in [(3, 31), (6, 30), (9, 30), (12, 31)]][:8]


def qd(year, quarter):
    m, d = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}[quarter]
    return f"{year}-{m:02d}-{d:02d} 00:00:00"


def cum(singles):
    total, out = 0, []
    for s in singles:
        total += s
        out.append(total)
    return out


def make_snap(with_balance=True, with_cashflow=True, revenue_2024=None):
    """手工数据按真实量级（元）：如 2024FY 收入 415 亿 = 415e8。"""
    e8 = lambda lst: [v * 1e8 for v in lst]
    dates = [qd(2023, q) for q in (1, 2, 3, 4)] + [qd(2024, q) for q in (1, 2, 3, 4)]
    rev = e8(cum([100] * 4) + cum([120, 110, 90, 95]))
    if revenue_2024 is not None:
        rev = e8(cum([100] * 4) + cum_from(revenue_2024))
    inc = standardize_statement("income", pd.DataFrame({
        REPORT_DATE_FIELD: dates,
        "OPERATE_INCOME": rev,
        "OPERATE_COST": e8(cum([60] * 4) + cum([70, 66, 54, 57])),
        "NETPROFIT": e8(cum([50] * 4) + cum([10, -5, -10, -15])),
        "PARENT_NETPROFIT": e8(cum([45] * 4) + cum([9, -4, -9, -13])),
    }))
    bal = standardize_statement("balance", pd.DataFrame({
        REPORT_DATE_FIELD: dates,
        "TOTAL_ASSETS": e8([1000] * 8), "TOTAL_LIABILITIES": e8([600] * 8),
        "TOTAL_CURRENT_ASSETS": e8([500] * 8), "TOTAL_CURRENT_LIAB": e8([400] * 8),
        "INVENTORY": e8([200 + i for i in range(8)]),
        "ACCOUNTS_RECE": e8([100, 100, 100, 100, 110, 150, 120, 130]),
        "TOTAL_EQUITY": e8([400] * 8),
    })) if with_balance else None
    cf = standardize_statement("cashflow", pd.DataFrame({
        REPORT_DATE_FIELD: dates,
        "NETCASH_OPERATE": e8(cum([40] * 4) + cum([-10, 30, 40, 50])),
    })) if with_cashflow else None
    return build_reference_snapshot("T", inc, bal, cf)


def cum_from(singles):
    total, out = 0, []
    for s in singles:
        total += s
        out.append(total)
    return out


def claim(**kw):
    base = dict(cid="C1", text="", location="", metric="revenue", metric_raw="营业收入",
                period="2024Q4", period_basis="cumulative", value=415e8, display="415亿元",
                unit="元", decimals=0, evidence_ref=None, kind="numeric")
    base.update(kw)
    return Claim(**base)


# ---------------- V1/V3 基础数值 ----------------

def test_correct_amount_passes():
    r = verify_numeric(claim(), make_snap(), None)
    assert r.status == "pass", r.diff_reason
    assert "415" in r.reference_display


def test_correct_ratio_passes():
    r = verify_numeric(claim(metric="gross_margin", metric_raw="毛利率",
                             value=0.405, display="40.5%", unit="%", decimals=1), make_snap(), None)
    assert r.status == "pass"


def test_correct_point_metric_passes():
    r = verify_numeric(claim(metric="total_assets", metric_raw="总资产",
                             value=1000e8, display="1000亿元"), make_snap(), None)
    assert r.status == "pass"


def test_wrong_metric_name_fails_with_hint():
    # 数字 415 亿是收入，写成净利润
    r = verify_numeric(claim(metric="net_profit", metric_raw="净利润"), make_snap(), None)
    assert r.status == "fail"
    assert "营业收入" in r.diff_reason and "指标名称误用" in r.diff_reason
    assert "-20" in r.reference_display or "20" in r.suggestion


def test_wrong_period_fails():
    # 415 亿是 2024 年收入，声称 2023 年
    r = verify_numeric(claim(period="2023Q4"), make_snap(), None)
    assert r.status == "fail"
    assert "400" in r.suggestion


def test_unit_conversion_error_fails():
    r = verify_numeric(claim(value=415e4, display="415万元"), make_snap(), None)
    assert r.status == "fail"
    assert "单位换算" in r.diff_reason


def test_sign_error_fails():
    # 净利 FY = −20 亿，声称 +20 亿
    r = verify_numeric(claim(metric="net_profit", metric_raw="净利润",
                             value=20e8, display="20亿元"), make_snap(), None)
    assert r.status == "fail"
    assert "正负号" in r.diff_reason


def test_missing_period_unable_to_verify():
    r = verify_numeric(claim(period=None, period_basis=""), make_snap(), None)
    assert r.status == "unable_to_verify"
    assert "期间" in r.diff_reason


def test_unknown_metric_unable_to_verify():
    r = verify_numeric(claim(metric="ebitda", metric_raw="EBITDA"), make_snap(), None)
    assert r.status == "unable_to_verify"


def test_missing_data_insufficient_not_false():
    # 无资产负债表 → 资产负债率无法取得参考
    r = verify_numeric(claim(metric="debt_to_asset_ratio", metric_raw="资产负债率",
                             value=0.6, display="60%", unit="%", decimals=0),
                       make_snap(with_balance=False), None)
    assert r.status == "insufficient_evidence"
    assert any("不等于声明虚假" in lim for lim in r.limitations)


def test_out_of_coverage_period_insufficient():
    r = verify_numeric(claim(period="2021Q4"), make_snap(), None)
    assert r.status == "insufficient_evidence"


# ---------------- 单季 vs 累计 ----------------

def test_cumulative_written_as_single_quarter_fails():
    # "二季度营业收入 230 亿"：Q2 单季 = 110 亿，230 是累计
    r = verify_numeric(claim(period="2024Q2", period_basis="quarter",
                             value=230e8, display="230亿元"), make_snap(), None)
    assert r.status == "fail"
    assert "累计" in r.diff_reason and "单季" in r.diff_reason
    assert "110" in r.suggestion


def test_single_quarter_correct_passes():
    r = verify_numeric(claim(period="2024Q2", period_basis="quarter",
                             value=110e8, display="110亿元"), make_snap(), None)
    assert r.status == "pass"


# ---------------- V2 同比与特殊状态 ----------------

def test_yoy_percent_correct_passes():
    r = verify_numeric(claim(metric="revenue_yoy", metric_raw="营业收入同比增长",
                             value=0.0375, display="3.75%", unit="%", decimals=2),
                       make_snap(), None)
    assert r.status == "pass", r.diff_reason


def test_yoy_percent_vs_pp_confusion_fails():
    # 参考同比 3.75%（比率），声称"上升 3.75 个百分点" → 概念混淆
    r = verify_numeric(claim(metric="revenue_yoy", metric_raw="营业收入同比增长",
                             value=3.75, display="3.75个百分点", unit="百分点", decimals=2),
                       make_snap(), None)
    assert r.status == "fail"
    assert "概念混淆" in r.diff_reason


def test_yoy_pp_unmatched_needs_review():
    # 百分点声称与参考百分比不匹配（无混淆证据）→ 保守人工复核
    r = verify_numeric(claim(metric="revenue_yoy", metric_raw="营业收入同比增长",
                             value=5.0, display="5个百分点", unit="百分点", decimals=1),
                       make_snap(), None)
    assert r.status == "needs_review"


def test_yoy_half_year_cumulative_passes():
    # 2024H1 收入同比 = 230/200 − 1 = 15%
    r = verify_numeric(claim(metric="revenue_yoy", metric_raw="营收同比",
                             period="2024Q2", value=0.15, display="15%", unit="%",
                             decimals=0), make_snap(), None)
    assert r.status == "pass", r.diff_reason


def test_status_word_match_and_mismatch():
    snap = make_snap()
    ok = verify_status_word(claim(metric="net_profit_yoy", metric_raw="净利润",
                                  display="由盈转亏", unit="status", kind="status_word"), snap)
    assert ok.status == "pass"
    bad = verify_status_word(claim(metric="net_profit_yoy", metric_raw="净利润",
                                   display="扭亏为盈", unit="status", kind="status_word"), snap)
    assert bad.status == "fail" and "profit_to_loss" in bad.diff_reason


# ---------------- V4 证据引用 ----------------

def _evidence_values(metric="revenue", period="2024Q4", value=415e8, code="T"):
    return {"E-REV-2024Q4": (code, period, metric, value)}


def test_evidence_not_exist_fails():
    r = verify_numeric(claim(evidence_ref="E-XXX-2099Q9"), make_snap(),
                       {"E-REV-2024Q4": ("T", "2024Q4", "revenue", 415e8)})
    # 数值本身正确（pass 路径）→ 引用检查发现不存在 → 改判 fail
    assert r.status == "fail" and "不存在" in r.diff_reason


def test_evidence_metric_mismatch_fails():
    # 声称净利润（数值写成收入 415 亿本身已 fail；换正确数值再验引用错配）：
    # 声明"净利润 −20 亿"（数值正确）但引用收入证据 → 字段不一致
    r = verify_numeric(claim(metric="net_profit", metric_raw="净利润",
                             value=-20e8, display="-20亿元",
                             evidence_ref="E-REV-2024Q4"), make_snap(),
                       _evidence_values())
    assert r.status == "fail"
    assert "字段" in r.diff_reason and "不一致" in r.diff_reason


def test_evidence_period_mismatch_fails():
    r = verify_numeric(claim(period="2023Q4", value=400e8, display="400亿元",
                             evidence_ref="E-REV-2024Q4"), make_snap(), _evidence_values())
    assert r.status == "fail" and "期间" in r.diff_reason


def test_evidence_company_mismatch_fails():
    r = verify_numeric(claim(evidence_ref="E-REV-2024Q4"), make_snap(),
                       _evidence_values(code="SH600000"))
    assert r.status == "fail" and "公司" in r.diff_reason


def test_evidence_consistent_passes_with_note():
    r = verify_numeric(claim(evidence_ref="E-REV-2024Q4"), make_snap(), _evidence_values())
    assert r.status == "pass"
    assert any("结构校验一致" in lim for lim in r.limitations)


# ---------------- 快照差异 ----------------

def test_snapshot_change_not_reported_as_error():
    # 报告生成时收入 415；参考数据已修订为 420 → 无法核验 + 限制说明
    snap2 = make_snap(revenue_2024=[120, 110, 90, 95 + 5])  # FY = 420
    r = verify_numeric(claim(), snap2, _evidence_values())
    assert r.status == "unable_to_verify"
    assert any("快照" in lim for lim in r.limitations)


# ---------------- V5 定性 ----------------

def test_qualitative_without_ref_insufficient():
    r = verify_qualitative(claim(kind="qualitative", metric="", value=None, unit="-",
                                 display="（定性结论）"))
    assert r.status == "insufficient_evidence"


def test_qualitative_with_ref_needs_review():
    r = verify_qualitative(claim(kind="qualitative", metric="", value=None, unit="-",
                                 display="（定性结论）", evidence_ref="E-REV-2024Q4"))
    assert r.status == "needs_review"
    assert any("语义" in lim or "语义" in r.diff_reason for lim in r.limitations)


def test_source_page_description_finding():
    findings = []
    check_source_descriptions("数据见公司2024年年报第 25 页披露。", findings)
    assert findings and "PDF 页码" in findings[0]
