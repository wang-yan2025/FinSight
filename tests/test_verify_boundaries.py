"""阶段6 最终验收边界测试：期间歧义、覆盖范围表述、快照隔离。"""
import sys
from pathlib import Path

import pandas as pd
import pytest

from finsight.finance.field_mapping import REPORT_DATE_FIELD, standardize_statement
from finsight.verify.claims import extract_claims
from finsight.verify.extract import extract_fragments
from finsight.verify.reference import build_reference_snapshot
from finsight.verify.report import report_to_markdown
from finsight.verify.rules import verify_claim_set, verify_numeric

sys.path.insert(0, str(Path(__file__).parent))
from test_verify_rules import claim, make_snap


# ---------------- 一、期间歧义 ----------------

def test_bare_quarter_tag_matching_cumulative_is_needs_review():
    # 裸 "2024Q4" + 数值恰等于累计参考（415 亿）→ 不得 pass，标 needs_review
    c = extract_claims(extract_fragments("r.md", "2024Q4营业收入为415亿元。".encode())).claims[0]
    assert c.period_basis == "quarter_report"
    r = verify_numeric(c, make_snap(), None)
    assert r.status == "needs_review", r.diff_reason
    assert "未注明单季/累计" in r.diff_reason
    assert any("累计参考" in lim and "单季参考" in lim for lim in r.limitations)


def test_bare_quarter_tag_matching_single_is_needs_review():
    c = extract_claims(extract_fragments("r.md", "2024Q4营业收入为95亿元。".encode())).claims[0]
    r = verify_numeric(c, make_snap(), None)
    assert r.status == "needs_review"
    assert any("单季参考" in lim for lim in r.limitations)


def test_bare_quarter_tag_matching_neither_is_fail():
    c = extract_claims(extract_fragments("r.md", "2024Q4营业收入为300亿元。".encode())).claims[0]
    r = verify_numeric(c, make_snap(), None)
    assert r.status == "fail"


def test_explicit_cumulative_keyword_disambiguates_to_pass():
    text = "2024Q4累计营业收入为415亿元。"
    c = extract_claims(extract_fragments("r.md", text.encode())).claims[0]
    assert c.period_basis == "cumulative"
    assert verify_numeric(c, make_snap(), None).status == "pass"


def test_explicit_quarter_wording_uses_single_basis():
    # "第四季度"常规读法为单季；数值等于单季参考 → pass
    c = extract_claims(extract_fragments(
        "r.md", "2024年第四季度营业收入为95亿元。".encode())).claims[0]
    assert c.period_basis == "quarter"
    assert verify_numeric(c, make_snap(), None).status == "pass"
    # "单季"关键词显式强调 → 同样按单季
    c2 = extract_claims(extract_fragments(
        "r.md", "2024Q4单季营业收入为95亿元。".encode())).claims[0]
    assert c2.period_basis == "quarter"
    assert verify_numeric(c2, make_snap(), None).status == "pass"


def test_explicit_quarter_without_single_reference_insufficient():
    # 数据缺 2023Q1 → 2023Q2 单季不可还原：明确单季声称不得回退用累计通过
    dates = [f"2023-06-30 00:00:00", "2023-09-30 00:00:00", "2023-12-31 00:00:00"]
    e8 = lambda lst: [v * 1e8 for v in lst]
    inc = standardize_statement("income", pd.DataFrame({
        REPORT_DATE_FIELD: dates, "OPERATE_INCOME": e8([300, 380, 400]),
    }))
    snap = build_reference_snapshot("T", inc, None, None)
    c = extract_claims(extract_fragments(
        "r.md", "2023年二季度营业收入为300亿元。".encode())).claims[0]
    assert c.period_basis == "quarter"
    r = verify_numeric(c, snap, None)
    assert r.status == "insufficient_evidence"   # 单季不可得，不得用累计 300 判 pass
    assert any("不等于声明虚假" in lim for lim in r.limitations)


def test_yoy_bare_quarter_tag_needs_review():
    c = claim(metric="revenue_yoy", metric_raw="营业收入同比", period_basis="quarter_report",
              value=0.0375, display="3.75%", unit="%", decimals=2)
    r = verify_numeric(c, make_snap(), None)
    assert r.status == "needs_review"
    assert any("累计参考" in lim or "单季参考" in lim for lim in r.limitations)


# ---------------- 二、覆盖范围与独立性表述 ----------------

def test_report_disclaimer_states_coverage_and_source_limits():
    report = verify_claim_set(
        extract_claims(extract_fragments("r.md", "2024年营业收入为415亿元。".encode())),
        make_snap(), target_desc="表述检查")
    md = report_to_markdown(report)
    assert "不代表报告全部内容已核验" in md
    assert "同源" in md and "非跨数据源交叉验证" in md
    assert "识别覆盖有限" in md
    # 不得出现"整份报告完全正确"式表述
    assert "完全正确" not in md.replace("不等于整份报告完全正确", "")


# ---------------- 三、快照隔离 ----------------

def test_unrelated_field_change_does_not_block_other_claims():
    # 证据登记中仅应收账款值变化：收入声明不受影响，仍可正常核验通过
    values = {"E-AR-2024Q4": ("T", "2024Q4", "accounts_receivable", 999e8),
              "E-REV-2024Q4": ("T", "2024Q4", "revenue", 415e8)}
    r = verify_numeric(claim(evidence_ref="E-REV-2024Q4"), make_snap(), values)
    assert r.status == "pass"


def test_company_isolation_in_evidence():
    # 他公司证据：公司不匹配 → fail，不得串用
    values = {"E-REV-2024Q4": ("SH600000", "2024Q4", "revenue", 415e8)}
    r = verify_numeric(claim(evidence_ref="E-REV-2024Q4"), make_snap(), values)
    assert r.status == "fail" and "公司" in r.diff_reason
