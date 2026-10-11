"""闭环数据结构：版本、轮次结果、修订建议、会话。"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field

from finsight.memo.skeleton import InvestmentMemo
from finsight.verify.claims import ClaimSet
from finsight.verify.rules import VerificationReportData


@dataclass
class ReportVersion:
    """报告版本。原始版本 parent=None；修订版本指向其父。"""

    vid: str                      # V1 / V2 / ...
    memo: InvestmentMemo
    markdown: str
    parent_vid: str | None = None
    created_reason: str = ""      # 初始生成 / 受控修订（采纳 N 条）


@dataclass
class RoundResult:
    """一轮独立核验的结果（绑定版本与参考快照指纹）。"""

    round_id: int                 # 1 / 2
    vid: str
    report: VerificationReportData
    claims: ClaimSet
    fingerprint: str              # 参考快照指纹（两轮一致才可比）


@dataclass
class RevisionProposal:
    """受控修订建议。

    deterministic=True 且位置唯一时给出 new_text（确定性替换文本）；
    否则仅人工建议。所有修订必须经用户确认（accepted）才会应用。
    """

    pid: str                      # P1 / P2 ...
    cid: str                      # 对应 Claim ID
    kind: str                     # numeric_fix / unit_fix / metric_fix /
                                  # basis_fix / evidence_fix / basis_clarify / manual_only
    deterministic: bool
    location: str
    original_sentence: str        # 原文整句（应用时的定位单位）
    old_display: str
    ref_display: str
    evidence_id: str | None = None
    reason: str = ""
    new_sentence: str | None = None   # 确定性替换后的整句（None=人工建议）
    new_display: str | None = None
    limitations: list[str] = dc_field(default_factory=list)
    accepted: bool = False

    def describe(self) -> str:
        arrow = f" → {self.new_display}" if self.new_display else " → （人工修订）"
        return f"{self.kind}: {self.old_display}{arrow}｜{self.reason}"


@dataclass
class AppliedRevision:
    """一条已应用修订的执行记录。"""

    pid: str
    cid: str
    applied: bool                 # 是否实际写入新版本
    note: str = ""                # 未应用原因（如位置不唯一）


@dataclass
class WorkflowSession:
    """一次闭环会话：同一公司、同一目标期间、同一参考快照。"""

    code: str
    period: str
    fingerprint: str
    snapshot: object = None            # ReferenceSnapshot（两轮核验共用同一对象）
    versions: dict[str, ReportVersion] = dc_field(default_factory=dict)
    rounds: dict[int, RoundResult] = dc_field(default_factory=dict)
    proposals: list[RevisionProposal] = dc_field(default_factory=list)
    applied: list[AppliedRevision] = dc_field(default_factory=list)

    @property
    def current_vid(self) -> str:
        return f"V{len(self.versions)}"
