"""闭环编排：生成 → 核验 → 修订 → 再核验 → 对比 的可测试流程函数。

快照一致性：会话内的所有核验共用构建时传入的同一个 ReferenceSnapshot
对象（指纹一致）；指纹变化时对比结果标记不可比，不归因为修订效果。
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field

from finsight.memo.evidence import build_evidence_book
from finsight.memo.render import memo_to_markdown
from finsight.memo.skeleton import InvestmentMemo, build_memo_skeleton
from finsight.verify.claims import ClaimSet, extract_claims
from finsight.verify.extract import Fragment
from finsight.verify.reference import ReferenceSnapshot, build_reference_snapshot
from finsight.verify.rules import VerificationReportData, verify_claim_set
from finsight.workflow.models import ReportVersion, RoundResult, WorkflowSession
from finsight.workflow.revision import apply_proposals, generate_proposals


# ---------------- 备忘录 ↔ 片段 / 证据登记（供核验与修订共用） ----------------

def memo_fragments(memo: InvestmentMemo) -> list[Fragment]:
    """备忘录正文 → 片段（章节标题作为 section，含 LLM 定性文本）。"""
    frags = []
    for sec in memo.sections:
        if sec.llm_text:
            frags.append(Fragment(sec.llm_text, f"章节「{sec.title}」（LLM 定性）", section=sec.title))
        for fact in sec.facts:
            frags.append(Fragment(fact, f"章节「{sec.title}」", section=sec.title))
    return frags


def memo_evidence_values(memo: InvestmentMemo) -> dict:
    """证据登记 → {eid: (公司, 期间, 指标, 值)}，供引用核验与快照差异检测。"""
    values = {}
    if memo is None or memo.evidence is None:
        return values
    metric_map = {"REV": "revenue", "NP": "net_profit", "PNP": "parent_net_profit",
                  "COST": "operate_cost", "OCF": "operating_cash_flow",
                  "ASSETS": "total_assets", "LIAB": "total_liabilities",
                  "AR": "accounts_receivable",
                  "REVYOY": "revenue_yoy", "NPYOY": "net_profit_yoy",
                  "PNPYOY": "parent_net_profit_yoy",
                  "GM": "gross_margin", "NM": "net_margin",
                  "DAR": "debt_to_asset_ratio", "CR": "current_ratio",
                  "ART": "receivables_turnover", "IVT": "inventory_turnover",
                  "OCFNPR": "ocf_np_ratio"}
    for eid, e in memo.evidence.evidences.items():
        parts = eid.split("-")
        metric = metric_map.get(parts[1]) if len(parts) > 2 else None
        period = parts[-1] if len(parts) > 2 else ""
        values[eid] = (memo.code, period, metric, e.value)
    return values


# ---------------- 编排步骤（纯函数，UI 只做按钮与状态管理） ----------------

def start_session(code: str, std_income, std_balance, std_cashflow,
                  sources: dict | None = None, target_date=None) -> WorkflowSession:
    """构建参考快照与初始版本（V1 基础备忘录）并完成首轮核验。"""
    snap = build_reference_snapshot(code, std_income, std_balance, std_cashflow,
                                    sources=sources)
    book = build_evidence_book(code, std_income, std_balance, std_cashflow,
                               sources=sources, target_date=target_date)
    memo = build_memo_skeleton(code, book)
    session = WorkflowSession(code=code, period=memo.period, fingerprint=snap.fingerprint,
                              snapshot=snap)
    v1 = ReportVersion(vid="V1", memo=memo, markdown=memo_to_markdown(memo),
                       created_reason="初始生成（确定性骨架）")
    session.versions[v1.vid] = v1
    session = verify_version(session, v1, snap, round_id=1)
    session.proposals = generate_proposals(session.rounds[1],
                                           memo_evidence_values(memo), memo)
    return session


def verify_version(session: WorkflowSession, version: ReportVersion,
                   snap: ReferenceSnapshot, round_id: int) -> WorkflowSession:
    """对指定版本执行独立核验（重新提取、重新比对，不复用旧结果）。"""
    claim_set = extract_claims(memo_fragments(version.memo))
    report = verify_claim_set(claim_set, snap,
                              snapshot_values=memo_evidence_values(version.memo),
                              fragments_text="\n".join(f.text for f in memo_fragments(version.memo)),
                              target_desc=f"{version.vid}（{session.code}，{session.period}）")
    session.rounds[round_id] = RoundResult(round_id=round_id, vid=version.vid,
                                           report=report, claims=claim_set,
                                           fingerprint=snap.fingerprint)
    return session


def revise(session: WorkflowSession, accepted_pids: list[str]) -> tuple[WorkflowSession, ReportVersion]:
    """按用户确认的修订建议生成新版本并再核验（使用会话内同一参考快照）。"""
    parent = session.versions[session.current_vid]
    for p in session.proposals:
        p.accepted = p.pid in accepted_pids
    child, applied = apply_proposals(parent, session.proposals)
    session.versions[child.vid] = child
    session.applied = applied
    session = verify_version(session, child, session.snapshot, round_id=len(session.rounds) + 1)
    return session, child


# ---------------- 两轮对比 ----------------

@dataclass
class ClaimComparison:
    cid: str
    round1_status: str
    round2_status: str | None
    outcome: str          # fixed / regression / clarified / unchanged / dropped /
                          # coverage_lost / new_claim
    note: str = ""


@dataclass
class ComparisonReport:
    session_code: str
    period: str
    v1: str
    v2: str
    fingerprint_match: bool = True
    comparable: bool = True
    snapshot_note: str = ""
    round1_counts: dict = dc_field(default_factory=dict)
    round2_counts: dict = dc_field(default_factory=dict)
    items: list[ClaimComparison] = dc_field(default_factory=list)

    def outcomes(self, outcome: str) -> list[ClaimComparison]:
        return [i for i in self.items if i.outcome == outcome]


def compare_rounds(session: WorkflowSession) -> ComparisonReport:
    """对比两轮核验：按（指标,期间,口径,kind）匹配声明；不可靠匹配如实标注。

    pass 增加不必然代表准确率提高：同时报告声明数变化、提取丢失与
    覆盖变化，未识别不计为通过。
    """
    r1, r2 = session.rounds[1], session.rounds[2]
    cmp_report = ComparisonReport(session_code=session.code, period=session.period,
                                  v1=r1.vid, v2=r2.vid,
                                  fingerprint_match=r1.fingerprint == r2.fingerprint,
                                  round1_counts=r1.report.counts(),
                                  round2_counts=r2.report.counts())
    if not cmp_report.fingerprint_match:
        cmp_report.comparable = False
        cmp_report.snapshot_note = ("两轮核验使用的参考数据快照不一致，"
                                    "结果差异不可归因于修订效果，请重新建立可比基准")
        return cmp_report
    cmp_report.snapshot_note = "两轮核验使用同一参考数据快照（指纹一致），结果可比"

    def key_of(claim, with_basis: bool = True):
        if with_basis:
            return (claim.kind, claim.metric, claim.period, claim.period_basis)
        return (claim.kind, claim.metric, claim.period)

    round2_exact = {key_of(c): c for c in r2.claims.claims}
    round2_loose = {key_of(c, False): c for c in r2.claims.claims}
    matched_r2_cids = set()

    def find_match(c1):
        m = round2_exact.get(key_of(c1))
        if m is not None:
            return m
        # 口径澄清类修订会改变 period_basis：宽松键兜底（指标+期间相同）
        return round2_loose.get(key_of(c1, False))

    for c1 in r1.claims.claims:
        s1 = next((r.status for r in r1.report.results if r.cid == c1.cid), None)
        match = find_match(c1)
        s2 = None
        if match is not None:
            matched_r2_cids.add(match.cid)
            s2 = next((r.status for r in r2.report.results if r.cid == match.cid), None)
        if match is None:
            # 未在第二轮出现：区分"修订后提取丢失"与"声明被删除"
            edited_cids = {a.cid for a in session.applied}
            if c1.cid in edited_cids:
                outcome, note = "coverage_lost", "该声明经修订后无法再被提取，不计为修复成功"
            else:
                outcome, note = "dropped", "声明未出现在修订稿（可能被删除），不计为修复"
            cmp_report.items.append(ClaimComparison(c1.cid, s1, None, outcome, note))
            continue
        if s1 == "fail" and s2 == "pass":
            outcome, note = "fixed", ""
        elif s1 == "pass" and s2 == "fail":
            outcome, note = "regression", "修订引入新错误"
        elif s1 == "needs_review" and s2 == "pass":
            outcome, note = "clarified", "口径澄清后通过（基于真实口径说明重新核验）"
        elif s1 == s2:
            outcome, note = "unchanged", ""
        else:
            outcome, note = "changed", f"{s1} → {s2}"
        cmp_report.items.append(ClaimComparison(c1.cid, s1, s2, outcome, note))
    for c2 in r2.claims.claims:
        if c2.cid not in matched_r2_cids:
            s2 = next((r.status for r in r2.report.results if r.cid == c2.cid), None)
            cmp_report.items.append(ClaimComparison(c2.cid, None, s2, "new_claim",
                                                    "修订稿中新出现的声明"))
    return cmp_report
