"""受控修订引擎：从核验结果生成修订建议，并按用户确认生成新版本。

红线：
- 不自动修改 pass 的正确事实；不把 insufficient/unable 当可确定性纠正；
- 不凭空生成数字（new_display 一律来自独立参考值）；不做全局替换；
- 位置不唯一 → 只给人工建议；用户未确认 → 原文不动（原始版本只读）。
"""
from __future__ import annotations

import copy
import re

from finsight.memo.skeleton import InvestmentMemo
from finsight.verify.reference import METRIC_REGISTRY
from finsight.workflow.models import AppliedRevision, ReportVersion, RevisionProposal

_METRIC_CN = {"revenue": "营业收入", "net_profit": "净利润", "parent_net_profit": "归母净利润",
              "operating_cash_flow": "经营活动现金流净额", "total_assets": "总资产",
              "total_liabilities": "总负债", "accounts_receivable": "应收账款", "inventory": "存货",
              "gross_margin": "毛利率", "net_margin": "净利率", "debt_to_asset_ratio": "资产负债率",
              "current_ratio": "流动比率", "receivables_turnover": "应收账款周转率",
              "inventory_turnover": "存货周转率",
              "revenue_yoy": "营业收入同比", "net_profit_yoy": "净利润同比",
              "parent_net_profit_yoy": "归母净利润同比", "ocf_np_ratio": "经营现金流/净利润"}

_AMOUNT_SCALES = {"亿元": 1e8, "万元": 1e4, "元": 1.0}


def _format_ref_display(ref_value: float, unit: str, decimals: int,
                        original_unit_word: str) -> str:
    """把参考值按原文的显示单位与精度格式化（不凭空造数）。"""
    if unit == "元":
        scale = _AMOUNT_SCALES.get(original_unit_word, 1.0)
        number = ref_value / scale
        text = f"{number:,.{decimals}f}" if decimals else f"{number:,.0f}"
        return f"{text}{original_unit_word}"
    if unit == "%":
        return f"{ref_value * 100:+.{max(decimals, 2)}f}%"
    return f"{ref_value:,.{max(decimals, 1)}f} {original_unit_word or unit}"


def _split_display(display: str) -> tuple[str, str] | None:
    m = re.match(r"(-?[\d,]+(?:\.\d+)?)(亿元|万元|元|个百分点|pp|％|%|倍|次)$", display)
    if not m:
        return None
    return m.group(1), m.group(2)


def _claim_lookup(claims, cid: str):
    return next((c for c in claims if c.cid == cid), None)


def generate_proposals(round_result, snapshot_values: dict | None,
                       memo: InvestmentMemo) -> list[RevisionProposal]:
    """从一轮核验结果生成修订建议。

    - fail：条件齐备（参考值/期间/口径确定 + 句子在全文唯一）时给确定性替换；
    - needs_review（期间口径歧义）：若报告自身证据能证明实际口径，
      给"补充口径说明"建议（同样需人工确认）；
    - insufficient / unable / 定性：仅人工建议说明。
    """
    proposals: list[RevisionProposal] = []
    full_text = "\n".join(f for s in memo.sections for f in s.facts) + "\n" + \
        "\n".join(s.llm_text or "" for s in memo.sections)

    for r in round_result.report.results:
        claim = _claim_lookup(round_result.claims.claims, r.cid)
        if claim is None:
            continue
        pid = f"P{len(proposals) + 1}"
        unique = full_text.count(claim.text) == 1

        if r.status == "pass":
            continue  # 正确事实不产生建议

        if r.status == "fail":
            proposal = _proposal_for_fail(pid, claim, r, unique, snapshot_values)
            if proposal:
                proposals.append(proposal)
            continue

        if r.status == "needs_review" and "未注明单季/累计" in r.diff_reason:
            proposals.append(_basis_clarify_proposal(pid, claim, r, unique, snapshot_values))
            continue

        # insufficient_evidence / unable_to_verify / 其他 needs_review → 人工建议
        proposals.append(RevisionProposal(
            pid=pid, cid=claim.cid, kind="manual_only", deterministic=False,
            location=r.location, original_sentence=claim.text,
            old_display=claim.display or "（定性/状态）",
            new_sentence=None, new_display=None,
            ref_display=r.reference_display or "—",
            evidence_id=claim.evidence_ref, reason=r.diff_reason or r.status,
            limitations=["该状态不可确定性纠正，需人工处理（不自动改写、不删除）"]
            + ([] if unique else ["原文位置不唯一，无法自动定位"])))
    return proposals


def _proposal_for_fail(pid, claim, r, unique: bool, snapshot_values) -> RevisionProposal | None:
    """确定性修订条件判定：指标/期间/口径/参考值可确定 + 位置唯一。"""
    reason = r.diff_reason
    if "单位换算" in reason:
        kind = "unit_fix"
    elif claim.evidence_ref and ("引用" in reason and ("不一致" in reason or "不存在" in reason or "期间" in reason or "公司" in reason)):
        kind = "evidence_fix"
    elif "指标名称误用" in reason:
        kind = "metric_fix"
    elif "误作单季" in reason:
        kind = "basis_fix"
    else:
        kind = "numeric_fix"

    base = RevisionProposal(
        pid=pid, cid=claim.cid, kind=kind, deterministic=False,
        location=r.location, original_sentence=claim.text,
        old_display=claim.display, ref_display=r.reference_display,
        evidence_id=claim.evidence_ref, reason=reason,
        limitations=list(r.limitations))
    if not unique:
        base.limitations.append("原文位置不唯一（同一句子多处出现），禁止自动替换，仅人工建议")
        return base
    if claim.value is None:
        base.limitations.append("声称数值不可解析，仅人工建议")
        return base

    # ---- 证据引用修正：存在唯一正确的替代证据时替换引用 ID ----
    if kind == "evidence_fix" and snapshot_values:
        tol_note = "数值须与替代证据一致"
        candidates = [eid for eid, (c, p, m, v) in snapshot_values.items()
                      if c == r.ref_source and False]  # 占位，下方按条件重写
        correct = [eid for eid, (code_v, period_v, metric_v, value_v) in snapshot_values.items()
                   if metric_v == claim.metric and claim.period and period_v == claim.period
                   and value_v is not None and claim.value is not None
                   and abs(value_v - claim.value) <= max(abs(value_v) * 1e-9, 1e-6)
                   and eid != claim.evidence_ref]
        if len(correct) == 1:
            new_sentence = claim.text.replace(f"[{claim.evidence_ref}]", f"[{correct[0]}]", 1)
            if new_sentence != claim.text:
                base.new_sentence = new_sentence
                base.new_display = f"[{claim.evidence_ref}] → [{correct[0]}]"
                base.deterministic = True
                return base
        base.limitations.append(f"替代证据不唯一或不存在（{tol_note}），仅人工建议")
        return base

    # 参考值解析：从参考展示文本还原数值（由引擎生成，非编造）
    ref_value = _ref_value_from_result(r, claim)
    if ref_value is None:
        base.limitations.append("无法从核验结果取得唯一参考数值，仅人工建议")
        return base

    display_parts = _split_display(claim.display or "")
    if display_parts is None:
        base.limitations.append("原文数值格式不支持自动重写，仅人工建议")
        return base
    _, unit_word = display_parts

    if kind == "unit_fix" and display_parts:
        # 单位词修正的前提：数字×修正单位 == 同一指标的参考值。
        # 不满足（如数字实为其他指标的数值）则回退为按参考值重写。
        number = float(display_parts[0].replace(",", ""))
        ref = ref_value
        for word, scale in _AMOUNT_SCALES.items():
            if abs(number * scale - ref) <= max(abs(ref) * 5e-3, 0.5 * scale):
                new_display = f"{display_parts[0]}{word}"
                base.new_display = new_display
                base.new_sentence = claim.text.replace(claim.display, new_display, 1)
                base.deterministic = True
                return base
        base.limitations.append("数字×任一单位均不等于本指标参考值"
                                "（数值可能属于其他指标），按参考值重写而非仅换单位")

    new_display = _format_ref_display(ref_value, claim.unit, claim.decimals, unit_word)
    if kind == "metric_fix":
        # 指标词误用：把句中指标词替换为正确指标词（词在句中唯一时）
        wrong = claim.metric_raw
        correct = _METRIC_CN.get(claim.metric)
        if correct and wrong and wrong != correct and claim.text.count(wrong) == 1:
            base.new_sentence = claim.text.replace(wrong, correct, 1)
            base.new_display = f"{wrong} → {correct}"
            base.deterministic = True
            return base
        base.limitations.append("指标词在句中不唯一或无法映射，仅人工建议")
        return base

    base.new_display = new_display
    base.new_sentence = claim.text.replace(claim.display, new_display, 1)
    base.deterministic = True
    return base


def _ref_value_from_result(r, claim) -> float | None:
    """从核验结果的参考展示/依据中还原参考数值。

    优先从 basis_detail 中的固定格式取数；失败则解析 reference_display。
    仅使用核验引擎写入的参考值，不另行计算。
    """
    kind_unit = METRIC_REGISTRY.get(claim.metric, (None, None, "-"))[2]
    m = re.search(r"独立重算参考 ([^（]+)（", r.basis_detail or "")
    if m and kind_unit != "-":
        text = m.group(1).strip()
        try:
            return _parse_ref_number(text, kind_unit)
        except ValueError:
            pass
    return _parse_display_value(r.reference_display)


def _parse_ref_number(text: str, unit: str) -> float:
    number = float(text.replace(",", "").split(" ")[0])
    if unit == "元":
        for word, scale in (("亿元", 1e8), ("万元", 1e4)):
            if word in text:
                return number * scale
        return number
    return number


def _parse_display_value(display: str) -> float | None:
    if not display:
        return None
    m = re.search(r"(-?[\d,]+(?:\.\d+)?)\s*(亿元|万元|%)?", display)
    if not m:
        return None
    value = float(m.group(1).replace(",", ""))
    word = m.group(2)
    if word == "亿元":
        value *= 1e8
    elif word == "万元":
        value *= 1e4
    elif word == "%":
        value /= 100.0
    return value


def _basis_clarify_proposal(pid, claim, r, unique, snapshot_values) -> RevisionProposal:
    """needs_review（口径歧义）：若报告自身证据能证明实际口径 → 补充口径说明建议。"""
    proposal = RevisionProposal(
        pid=pid, cid=claim.cid, kind="basis_clarify", deterministic=False,
        location=r.location, original_sentence=claim.text,
        old_display=claim.display, ref_display=r.reference_display,
        evidence_id=claim.evidence_ref, reason=r.diff_reason,
        limitations=list(r.limitations) + ["补充口径说明不改变数值本身，需人工确认后应用"])
    # 核验结果的 ref_basis 由引擎按实际匹配口径写明（单季同比/累计同比/单季值/累计值）
    basis_word = "单季" if "单季" in r.ref_basis else ("累计" if "累计" in r.ref_basis else None)
    if basis_word and unique and claim.period:
        tag = claim.period
        if claim.text.count(tag) == 1:
            proposal.new_sentence = claim.text.replace(tag, f"{tag}（{basis_word}口径）", 1)
            proposal.new_display = f"{tag} → {tag}（{basis_word}口径）"
            proposal.deterministic = True
        else:
            proposal.limitations.append("期间标签在句中出现多次，无法唯一定位，仅人工建议")
    elif not basis_word:
        proposal.limitations.append("无法从证据确定实际口径，保留人工复核")
    return proposal


def apply_proposals(parent: ReportVersion, proposals: list[RevisionProposal],
                    reason: str = "") -> tuple[ReportVersion, list[AppliedRevision]]:
    """按用户确认（accepted=True）的确定性建议生成新版本。

    只替换整句（先全文唯一性复核），不做子串全局替换；未应用的建议记录原因。
    原始 memo 不被修改（深拷贝后编辑）。
    """
    new_memo: InvestmentMemo = copy.deepcopy(parent.memo)
    applied: list[AppliedRevision] = []

    def all_texts():
        return [f for s in new_memo.sections for f in s.facts] + \
               [s.llm_text for s in new_memo.sections if s.llm_text]

    for p in proposals:
        if not p.accepted:
            applied.append(AppliedRevision(p.pid, p.cid, applied=False, note="用户未采纳"))
            continue
        if not p.deterministic or not p.new_sentence:
            applied.append(AppliedRevision(p.pid, p.cid, applied=False,
                                           note="非确定性建议，需人工修订"))
            continue
        occurrences = sum(t.count(p.original_sentence) for t in all_texts())
        if occurrences != 1:
            applied.append(AppliedRevision(p.pid, p.cid, applied=False,
                                           note=f"位置不唯一（{occurrences} 处），禁止替换"))
            continue
        hit = False
        for section in new_memo.sections:
            for i, fact in enumerate(section.facts):
                if p.original_sentence in fact:
                    section.facts[i] = fact.replace(p.original_sentence, p.new_sentence, 1)
                    hit = True
                    break
            if hit:
                break
            if section.llm_text and p.original_sentence in section.llm_text:
                section.llm_text = section.llm_text.replace(
                    p.original_sentence, p.new_sentence, 1)
                hit = True
                break
        applied.append(AppliedRevision(p.pid, p.cid, applied=hit,
                                       note="" if hit else "未在新版本中定位到原文"))
    from finsight.memo.render import memo_to_markdown
    accepted_count = sum(1 for a in applied if a.applied)
    child = ReportVersion(
        vid=f"V{int(parent.vid[1:]) + 1}",
        memo=new_memo, markdown=memo_to_markdown(new_memo), parent_vid=parent.vid,
        created_reason=reason or f"受控修订（实际应用 {accepted_count} 条）")
    return child, applied
