"""闭环导出：修订记录与两轮对比摘要（Markdown）。"""
from __future__ import annotations

from finsight.verify.report import VERIFY_DISCLAIMER
from finsight.workflow.models import WorkflowSession
from finsight.workflow.pipeline import ComparisonReport, compare_rounds

_OUTCOME_CN = {"fixed": "已修复", "regression": "新引入错误", "clarified": "口径澄清后通过",
               "unchanged": "保持不变", "changed": "状态变化", "dropped": "声明被删除",
               "coverage_lost": "修订后提取丢失", "new_claim": "新出现声明"}


def revisions_to_markdown(session: WorkflowSession) -> str:
    lines = [f"# 修订记录——{session.code}（{session.period}）", "",
             f"- 数据快照指纹：{session.fingerprint}",
             f"- 版本链：{' → '.join(session.versions)}", ""]
    if not session.proposals:
        lines.append("首轮核验未产生修订建议（无 fail / 口径歧义声明）。")
    for p in session.proposals:
        applied = next((a for a in session.applied if a.pid == p.pid), None)
        state = ("已应用" if applied and applied.applied else
                 "已确认未应用：" + applied.note if applied else
                 ("用户未采纳" if not p.accepted else "待确认"))
        lines += [
            f"## {p.pid}（{p.kind}，{'确定性建议' if p.deterministic else '人工建议'}）",
            f"- 声明：{p.cid}｜状态执行：{state}",
            f"- 原文（{p.location}）：{p.original_sentence}",
            f"- 修改：{p.old_display} → {p.new_display or '（人工修订）'}",
            f"- 参考依据：{p.ref_display}" + (f"（证据 {p.evidence_id}）" if p.evidence_id else ""),
            f"- 原因：{p.reason}",
        ]
        for lim in p.limitations:
            lines.append(f"- 限制：{lim}")
        lines.append("")
    lines.append(f"**声明**：{VERIFY_DISCLAIMER}")
    return "\n".join(lines)


def comparison_to_markdown(session: WorkflowSession) -> str:
    cmp_report: ComparisonReport = compare_rounds(session)
    r1c, r2c = cmp_report.round1_counts, cmp_report.round2_counts
    lines = [
        f"# 核验前后对比——{session.code}（{session.period}）", "",
        f"- 版本：{cmp_report.v1}（原始） vs {cmp_report.v2}（修订）",
        f"- 快照可比性：{cmp_report.snapshot_note}",
        "",
        "| 状态 | 首轮 | 再核验 | 差值 |", "|---|---|---|---|",
    ]
    for status in ("pass", "fail", "insufficient_evidence", "unable_to_verify", "needs_review"):
        d = r2c.get(status, 0) - r1c.get(status, 0)
        lines.append(f"| {status} | {r1c.get(status, 0)} | {r2c.get(status, 0)} | {d:+d} |")
    lines += ["",
              "> pass 增加不必然代表准确率提高；请同时关注声明数、提取覆盖与下列明细：", ""]
    for outcome in ("fixed", "regression", "clarified", "coverage_lost", "dropped",
                    "new_claim", "unchanged", "changed"):
        group = cmp_report.outcomes(outcome)
        if not group:
            continue
        lines.append(f"## {_OUTCOME_CN[outcome]}（{len(group)}）")
        for item in group:
            lines.append(f"- `{item.cid}`：{item.round1_status} → {item.round2_status}"
                         + (f"（{item.note}）" if item.note else ""))
        lines.append("")
    lines.append(f"**声明**：{VERIFY_DISCLAIMER}")
    return "\n".join(lines)
