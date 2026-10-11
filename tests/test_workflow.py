"""闭环工作流测试：修订引擎、两轮对比、快照隔离（固定手算数据，离线）。"""
import pandas as pd
import pytest

from finsight.finance.field_mapping import REPORT_DATE_FIELD, standardize_statement
from finsight.verify.claims import extract_claims
from finsight.verify.extract import extract_fragments
from finsight.verify.reference import build_reference_snapshot
from finsight.verify.rules import verify_claim_set
from finsight.workflow.pipeline import compare_rounds, start_session, verify_version
from finsight.workflow.revision import apply_proposals, generate_proposals


def qd(year, quarter):
    m, d = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}[quarter]
    return f"{year}-{m:02d}-{d:02d} 00:00:00"


def cum(singles):
    total, out = 0, []
    for s in singles:
        total += s
        out.append(total)
    return out


E8 = lambda lst: [v * 1e8 for v in lst]
DATES = [qd(2023, q) for q in (1, 2, 3, 4)] + [qd(2024, q) for q in (1, 2, 3, 4)]


def make_frames():
    inc = standardize_statement("income", pd.DataFrame({
        REPORT_DATE_FIELD: DATES,
        "OPERATE_INCOME": E8(cum([100] * 4) + cum([120, 110, 90, 95])),
        "OPERATE_COST": E8(cum([60] * 4) + cum([70, 66, 54, 57])),
        "NETPROFIT": E8(cum([50] * 4) + cum([10, -5, -10, -15])),
        "PARENT_NETPROFIT": E8(cum([45] * 4) + cum([9, -4, -9, -13])),
    }))
    bal = standardize_statement("balance", pd.DataFrame({
        REPORT_DATE_FIELD: DATES,
        "TOTAL_ASSETS": E8([1000] * 8), "TOTAL_LIABILITIES": E8([600] * 8),
        "TOTAL_CURRENT_ASSETS": E8([500] * 8), "TOTAL_CURRENT_LIAB": E8([400] * 8),
        "INVENTORY": E8([200 + i for i in range(8)]),
        "ACCOUNTS_RECE": E8([100, 100, 100, 100, 110, 150, 120, 130]),
        "TOTAL_EQUITY": E8([400] * 8),
    }))
    cf = standardize_statement("cashflow", pd.DataFrame({
        REPORT_DATE_FIELD: DATES,
        "NETCASH_OPERATE": E8(cum([40] * 4) + cum([-10, 30, 40, 50])),
    }))
    return inc, bal, cf


@pytest.fixture()
def session():
    inc, bal, cf = make_frames()
    s = start_session("T", inc, bal, cf)
    return s


# ---------------- 生成与首轮核验 ----------------

def test_session_starts_with_v1_and_round1(session):
    assert "V1" in session.versions and session.versions["V1"].parent_vid is None
    assert 1 in session.rounds and session.rounds[1].vid == "V1"
    assert session.rounds[1].fingerprint == session.fingerprint
    # 生成端口径显式化：风险条件句携带（单季口径/累计口径）标注
    md = session.versions["V1"].markdown
    assert "口径）" in md  # 信号口径标注存在


def test_signal_basis_labels_use_engine_output(session):
    """R2 单季口径条件句在核验中应能按单季同比通过（口径歧义消除）。"""
    r1 = session.rounds[1]
    yoy_needs = [r for r in r1.report.results
                 if "未注明单季/累计" in (r.diff_reason or "")]
    # 生成端已显式标注口径 → 不应再有因裸期间标签导致的口径歧义
    assert yoy_needs == [], [r.diff_reason for r in yoy_needs]


# ---------------- 修订建议生成 ----------------

def test_no_proposal_for_correct_facts(session):
    correct_cids = {r.cid for r in session.rounds[1].report.results if r.status == "pass"}
    proposal_cids = {p.cid for p in session.proposals}
    assert not (correct_cids & proposal_cids), "pass 事实不得产生修订建议"


def test_deterministic_proposal_for_amount_error():
    """手工构造含金额错误的样本：建议值必须等于人工预期 415 亿元。"""
    from finsight.memo.skeleton import InvestmentMemo, MemoSection
    from finsight.workflow.models import RoundResult

    inc, _, _ = make_frames()
    snap = build_reference_snapshot("T", inc, None, None)
    report_md = "2024年营业收入为480亿元。"
    claim_set = extract_claims(extract_fragments("r.md", report_md.encode()))
    result = verify_claim_set(claim_set, snap, target_desc="t")
    memo = InvestmentMemo(code="T", period="2024Q4", generated_at="-")
    memo.sections.append(MemoSection("financials", "三、财务表现", "deterministic",
                                     facts=[report_md]))
    proposals = generate_proposals(
        RoundResult(1, "V1", result, claim_set, snap.fingerprint), None, memo)
    assert len(proposals) == 1 and proposals[0].deterministic
    assert proposals[0].new_display == "415亿元"          # 人工预期（独立手算）
    # 句子在提取时按标点切分（不含句号）；应用时按子串替换，标点保留
    assert proposals[0].new_sentence == "2024年营业收入为415亿元"


def test_non_unique_location_forbids_auto_replace():
    from finsight.memo.skeleton import InvestmentMemo, MemoSection
    from finsight.workflow.models import RoundResult

    inc, _, _ = make_frames()
    snap = build_reference_snapshot("T", inc, None, None)
    sentence = "2024年营业收入为480亿元。"
    claim_set = extract_claims(extract_fragments("r.md", sentence.encode()))
    result = verify_claim_set(claim_set, snap, target_desc="t")
    memo = InvestmentMemo(code="T", period="2024Q4", generated_at="-")
    memo.sections.append(MemoSection("financials", "三、财务表现", "deterministic",
                                     facts=[sentence, sentence]))  # 两处出现
    proposals = generate_proposals(
        RoundResult(1, "V1", result, claim_set, snap.fingerprint), None, memo)
    assert not proposals[0].deterministic
    assert any("位置不唯一" in lim for lim in proposals[0].limitations)


def test_missing_reference_forbids_fabrication():
    """超范围期间（insufficient）不得生成替代数字建议。"""
    from finsight.memo.skeleton import InvestmentMemo, MemoSection
    from finsight.workflow.models import RoundResult

    inc, _, _ = make_frames()
    snap = build_reference_snapshot("T", inc, None, None)
    sentence = "2021年营业收入为300亿元。"
    claim_set = extract_claims(extract_fragments("r.md", sentence.encode()))
    result = verify_claim_set(claim_set, snap, target_desc="t")
    memo = InvestmentMemo(code="T", period="2024Q4", generated_at="-")
    memo.sections.append(MemoSection("financials", "三、财务表现", "deterministic",
                                     facts=[sentence]))
    proposals = generate_proposals(
        RoundResult(1, "V1", result, claim_set, snap.fingerprint), None, memo)
    assert proposals[0].kind == "manual_only" and proposals[0].new_sentence is None


# ---------------- 应用修订与再核验 ----------------

def test_rejected_proposal_keeps_original(session):
    """用户全部拒绝：不生成新版本，V1 原文不变。"""
    from finsight.workflow.pipeline import revise
    before = session.versions["V1"].markdown
    if not session.proposals:
        pytest.skip("无修订建议场景")
    revised, _ = revise(session, accepted_pids=[])
    assert revised.versions["V1"].markdown == before
    assert all(a.applied is False for a in revised.applied)


def test_full_loop_fix_and_recheck():
    """完整闭环（人工预期驱动）：金额错误 → 确认修订 → 再核验通过 → 对比=已修复。"""
    from finsight.workflow.pipeline import revise
    from finsight.memo.skeleton import InvestmentMemo, MemoSection
    from finsight.workflow.models import RoundResult

    inc, _, _ = make_frames()
    snap = build_reference_snapshot("T", inc, None, None)
    wrong = "2024年营业收入为480亿元。"
    right = "2024年净利润为-20亿元。"
    memo = InvestmentMemo(code="T", period="2024Q4", generated_at="-")
    memo.sections.append(MemoSection("financials", "三、财务表现", "deterministic",
                                     facts=[wrong, right]))
    claim_set = extract_claims(extract_fragments("r.md", f"{wrong}{right}".encode()))
    result = verify_claim_set(claim_set, snap, target_desc="t")
    session = type("S", (), {})()  # 轻量会话载体
    from finsight.workflow.models import WorkflowSession
    session = WorkflowSession(code="T", period="2024Q4", fingerprint=snap.fingerprint,
                              snapshot=snap)
    session.versions["V1"] = type("V", (), {"vid": "V1", "memo": memo,
                                            "markdown": wrong + right,
                                            "parent_vid": None, "created_reason": "t"})()
    session.rounds[1] = RoundResult(1, "V1", result, claim_set, snap.fingerprint)
    proposals = generate_proposals(session.rounds[1], None, memo)
    assert any(p.deterministic and p.new_display == "415亿元" for p in proposals)

    from finsight.workflow.models import ReportVersion
    session.versions["V1"] = ReportVersion("V1", memo, wrong + right)
    session.proposals = proposals
    session, child = revise(session, [p.pid for p in proposals if p.deterministic])
    # 再核验为独立执行：修订句转 pass，正确句保持 pass
    r2 = session.rounds[2]
    statuses = {c.metric: next(x.status for x in r2.report.results if x.cid == c.cid)
                for c in r2.claims.claims}
    assert statuses["revenue"] == "pass"
    assert statuses["net_profit"] == "pass"
    # 对比：修复 1 条、无新错误；原始 V1 未被修改
    cmp_report = compare_rounds(session)
    assert len(cmp_report.outcomes("fixed")) == 1
    assert cmp_report.outcomes("regression") == []
    assert "480" not in child.markdown and "480" in session.versions["V1"].markdown


def test_recheck_not_copy_of_round1():
    """再核验必须是独立执行：重新提取声明对象，不复用首轮 ClaimSet/结果。"""
    from finsight.workflow.pipeline import revise
    from finsight.memo.skeleton import InvestmentMemo, MemoSection
    from finsight.workflow.models import ReportVersion, RoundResult, WorkflowSession

    inc, _, _ = make_frames()
    snap = build_reference_snapshot("T", inc, None, None)
    sentence = "2024年营业收入为480亿元。"
    memo = InvestmentMemo(code="T", period="2024Q4", generated_at="-")
    memo.sections.append(MemoSection("financials", "三、财务表现", "deterministic",
                                     facts=[sentence]))
    claim_set = extract_claims(extract_fragments("r.md", sentence.encode()))
    result = verify_claim_set(claim_set, snap, target_desc="t")
    session = WorkflowSession(code="T", period="2024Q4", fingerprint=snap.fingerprint,
                              snapshot=snap)
    session.versions["V1"] = ReportVersion("V1", memo, sentence)
    session.rounds[1] = RoundResult(1, "V1", result, claim_set, snap.fingerprint)
    session.proposals = generate_proposals(session.rounds[1], None, memo)
    session, _ = revise(session, [session.proposals[0].pid])
    r1, r2 = session.rounds[1], session.rounds[2]
    assert r2.claims is not r1.claims and r2.report is not r1.report  # 独立重跑
    assert r2.report.results[0].status == "pass"  # 修订 415 后独立再核验通过
    assert r2.claims.claims[0].value == 415e8     # 声明值来自修订稿重新提取


def test_snapshot_mismatch_marks_incomparable():
    """两轮快照指纹不同 → 对比标记不可比，不归因修订。"""
    from finsight.workflow.models import ReportVersion, RoundResult, WorkflowSession
    from finsight.verify.claims import ClaimSet

    session = WorkflowSession(code="T", period="2024Q4", fingerprint="aaa")
    session.versions["V1"] = ReportVersion("V1", None, "x")
    session.versions["V2"] = ReportVersion("V2", None, "y", parent_vid="V1")
    dummy = ClaimSet()
    empty = verify_claim_set(dummy, type("S", (), {"yoy": {}, "yoy_single": {},
                                                   "cumulative": {}, "single_quarter": {},
                                                   "coverage": ("", ""), "code": "T",
                                                   "value": lambda *a: None,
                                                   "signals": []})(), target_desc="x")
    session.rounds[1] = RoundResult(1, "V1", empty, dummy, "aaa")
    session.rounds[2] = RoundResult(2, "V2", empty, dummy, "bbb")   # 指纹不同
    cmp_report = compare_rounds(session)
    assert cmp_report.fingerprint_match is False and cmp_report.comparable is False
    assert "不可归因于修订" in cmp_report.snapshot_note


def test_company_and_period_isolation():
    inc, bal, cf = make_frames()
    s_a = start_session("AAA", inc, bal, cf)
    s_b = start_session("BBB", inc, bal, cf)
    assert s_a.code == "AAA" and s_b.code == "BBB"
    # 证据登记携带各自公司代码
    from finsight.workflow.pipeline import memo_evidence_values
    codes_a = {v[0] for v in memo_evidence_values(s_a.versions["V1"].memo).values()}
    codes_b = {v[0] for v in memo_evidence_values(s_b.versions["V1"].memo).values()}
    assert codes_a == {"AAA"} and codes_b == {"BBB"}
