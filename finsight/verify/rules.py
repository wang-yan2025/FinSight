"""核验规则引擎 V1—V5：独立比对报告声明与参考快照。

原则：
- pass 仅在数值、指标、单位、期间、公司、引用关系全部一致时给出；
- 缺数据/缺期间/口径不明 → unable_to_verify 或 insufficient_evidence，
  绝不默认通过；
- 定性内容与语义支持关系 → needs_review（保守，不用关键词匹配宣称通过）；
- 报告生成后参考数据变化（快照差异）→ limitation 说明，不判错误。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field

from finsight.verify.claims import Claim, ClaimSet
from finsight.verify.reference import METRIC_REGISTRY, ReferenceSnapshot

STATUS_ORDER = ["pass", "fail", "insufficient_evidence", "unable_to_verify", "needs_review"]
STATUS_CN = {"pass": "通过", "fail": "不通过（发现错误）",
             "insufficient_evidence": "证据不足", "unable_to_verify": "无法核验",
             "needs_review": "需人工复核"}

# 比率/同比类指标的允许单位
_RATIO_METRICS = {m for m, (kind, _, _) in METRIC_REGISTRY.items() if kind in ("ratio", "yoy")}
_FLOW_METRICS = {m for m, (kind, _, _) in METRIC_REGISTRY.items() if kind == "flow"}
_POINT_METRICS = {m for m, (kind, _, _) in METRIC_REGISTRY.items() if kind == "point"}


@dataclass
class VerificationResult:
    cid: str
    status: str
    claim_text: str
    location: str
    claimed_display: str = ""
    reference_display: str = ""
    difference: str = ""
    diff_reason: str = ""
    ref_period: str = ""
    ref_basis: str = ""
    ref_source: str = "东方财富三大报表（经 akshare，本地缓存快照）+ 既有指标引擎独立重算"
    basis_detail: str = ""
    suggestion: str = ""
    limitations: list[str] = dc_field(default_factory=list)


@dataclass
class VerificationReportData:
    target_desc: str                 # 核验对象描述
    code: str
    claim_count: int
    results: list[VerificationResult] = dc_field(default_factory=list)
    source_findings: list[str] = dc_field(default_factory=list)  # V4 来源描述类发现
    snapshot_note: str = ""

    def counts(self) -> dict[str, int]:
        return {s: sum(1 for r in self.results if r.status == s) for s in STATUS_ORDER}


# ---------------- 工具 ----------------

def _fmt_amount(v: float) -> str:
    for scale, name in ((1e8, "亿元"), (1e4, "万元")):
        if abs(v) >= scale:
            return f"{v / scale:,.4g} {name}"
    return f"{v:,.0f} 元"


def _fmt_value(v: float, unit: str) -> str:
    if unit == "元":
        return _fmt_amount(v)
    if unit in ("%",):
        return f"{v * 100:.2f}%"
    return f"{v:,.4g} {unit}"


def _tolerance(claim: Claim, unit: str) -> float:
    """按声称显示精度确定容差（半位舍入误差 + 微小 epsilon）。"""
    eps = 10 ** (-claim.decimals) * 0.5 if claim.decimals else 0.5
    if unit == "元":
        # 声称以亿元/万元等显示：容差按该显示单位的半位折算
        display_unit = re.search(r"(亿元|万元|元)", claim.display)
        scale = {"亿元": 1e8, "万元": 1e4, "元": 1.0}.get(display_unit.group(1), 1.0) \
            if display_unit else 1.0
        return eps * scale + 1e-6
    if unit in ("%",):
        return eps / 100.0 + 1e-9
    return eps + 1e-9


def _unit_conversion_hint(claim_value: float, ref_value: float) -> str | None:
    """数值恰好是参考值的 1e±4 / 1e±8 倍 → 单位换算错误提示。"""
    if ref_value == 0 or claim_value == 0:
        return None
    ratio = claim_value / ref_value
    for factor, hint in ((1e4, "疑似亿元/万元单位换算错误"),
                         (1e-4, "疑似万元/亿元单位换算错误"),
                         (1e8, "疑似亿元/元单位换算错误"),
                         (1e-8, "疑似元/亿元单位换算错误")):
        if abs(ratio - factor) / factor < 0.02:
            return hint
    return None


# ---------------- V1/V2/V3：数值与比率核验 ----------------

def verify_numeric(claim: Claim, snap: ReferenceSnapshot,
                   snapshot_values: dict | None) -> VerificationResult:
    """数值类声明核验（金额/比率/同比/周转，含单位与期间）。"""
    result = VerificationResult(cid=claim.cid, status="unable_to_verify",
                                claim_text=claim.text, location=claim.location,
                                claimed_display=claim.display)
    if claim.metric not in METRIC_REGISTRY:
        result.diff_reason = f"指标「{claim.metric_raw}」不在可核验指标范围内"
        result.suggestion = "补充该指标的原始财务数据或改用可核验表述"
        return result
    if claim.period is None:
        result.diff_reason = "声明未包含可识别的报告期间，无法确定核对基准"
        result.suggestion = "补充报告期间（如 2024 年 / 2024Q4）"
        return result
    kind = METRIC_REGISTRY[claim.metric][0]
    unit = METRIC_REGISTRY[claim.metric][2]

    # ---- 单位类别一致性（V3）----
    if kind in ("flow", "point") and claim.unit not in ("元",):
        result.diff_reason = f"指标「{claim.metric_raw}」为金额类（元），声明单位为 {claim.unit}"
        result.suggestion = "核实金额单位（元/万元/亿元）"
        return result
    if claim.metric in _RATIO_METRICS and claim.unit not in ("%", "百分点", "倍", "次"):
        result.diff_reason = f"比率类指标声明单位异常：{claim.unit}"
        return result

    # ---- 期间覆盖（insufficient ≠ 虚假）----
    # 口径解析：quarter=明确单季（仅单季口径）；cumulative=明确累计（仅累计口径）；
    # quarter_report=歧义标签（双口径取参考，匹配也不判 pass，标 needs_review）
    if claim.period_basis == "quarter":
        allowed_bases = ["single_quarter"]
    elif claim.period_basis == "cumulative":
        allowed_bases = ["cumulative"]
    else:
        allowed_bases = ["cumulative", "single_quarter"]

    if kind == "yoy":
        candidates = []
        for b in allowed_bases:
            table = snap.yoy if b == "cumulative" else snap.yoy_single
            entry = table.get((claim.metric, claim.period))
            if entry:
                candidates.append((entry, b))
        if not candidates:
            result.status = "insufficient_evidence"
            result.diff_reason = (f"参考数据无 {claim.period} 的{claim.metric_raw}数据"
                                  f"（覆盖范围 {snap.coverage[0]}–{snap.coverage[1]}）")
            result.limitations.append("数据不足不等于声明虚假")
            return result
        (ref_value, ref_status), used_basis = candidates[0]
        result.ref_period = claim.period

        # 百分点 vs 百分比概念混淆（V3）
        if claim.unit == "百分点":
            if any(abs(claim.value - v * 100) <= max(_tolerance(claim, "%") * 100, 0.05)
                   for (v, _), _ in candidates):
                result.status = "fail"
                result.diff_reason = (f"百分比与百分点概念混淆：参考同比为 {ref_value * 100:+.2f}%"
                                      "（比率），并非上升/下降该数值的百分点")
                result.suggestion = f"改写为「同比{'增长' if ref_value >= 0 else '下降'}{abs(ref_value * 100):.2f}%」"
                return result
            result.status = "needs_review"
            result.diff_reason = "百分点变动涉及比较基期语义，程序无法确定性核验"
            result.limitations.append("请人工核对百分点变动的比较基期与口径")
            return result

        tol = _tolerance(claim, "%")
        matched_entry = next(((v, s, b) for (v, s), b in candidates if abs(claim.value - v) <= tol),
                             None)
        if matched_entry:
            ref_value, ref_status, used_basis = matched_entry
            result.ref_basis = "累计同比" if used_basis == "cumulative" else "单季同比"
            result.reference_display = f"{ref_value * 100:+.2f}%（状态：{ref_status}）"
            if claim.period_basis == "quarter_report" and len(candidates) > 1:
                # 歧义期间：数值恰与某一口径一致，但不能确定原文指哪一口径
                both = "；".join(
                    f"{'累计' if b == 'cumulative' else '单季'}参考 {v * 100:+.2f}%"
                    for (v, _s), b in candidates)
                result.status = "needs_review"
                result.diff_reason = (f"期间标签 {claim.period} 未注明单季/累计口径，"
                                      f"数值与{result.ref_basis}一致，但两种读法均有可能")
                result.limitations.append(f"{both}；建议报告明确标注口径后复核")
            else:
                result.status = "pass"
                result.basis_detail = (f"参考同比 {ref_value * 100:+.2f}%（{result.ref_basis}），"
                                       f"状态分类 {ref_status}（yoy_status）")
            return result
        result.ref_basis = "累计同比" if used_basis == "cumulative" else "单季同比"
        result.reference_display = f"{ref_value * 100:+.2f}%（状态：{ref_status}）"
        result.status = "fail"
        result.difference = f"声称 {claim.value * 100:+.2f}% vs 参考 {ref_value * 100:+.2f}%"
        result.diff_reason = "同比数值与独立重算参考值不符"
        result.suggestion = f"建议改为 {ref_value * 100:+.2f}%（{claim.period}，{result.ref_basis}）"
        _snapshot_check(result, claim, snapshot_values, snap)
        return result

    # ---- 非同比指标（flow/point/ratio）----
    # point（时点余额）只有一种读法；flow/ratio 按声称口径限定候选
    if kind == "point":
        bases = ["cumulative"]
    else:
        bases = allowed_bases
    candidates = []
    for b in bases:
        v = snap.value(claim.metric, claim.period, b)
        if v is not None:
            candidates.append((v, b))
    if not candidates:
        result.status = "insufficient_evidence"
        result.diff_reason = (f"参考数据无 {claim.period} 的{claim.metric_raw}（覆盖 "
                              f"{snap.coverage[0]}–{snap.coverage[1]}）")
        result.limitations.append("数据不足不等于声明虚假")
        return result
    ref_value, used_basis = candidates[0]

    result.ref_period = claim.period
    result.ref_basis = {"flow": {"cumulative": "累计值", "single_quarter": "单季值"},
                        "point": {"cumulative": "期末时点值", "single_quarter": "期末时点值"},
                        "ratio": {"cumulative": "累计口径", "single_quarter": "单季口径"}}[kind][used_basis]
    result.reference_display = _fmt_value(ref_value, unit)
    tol = _tolerance(claim, "元" if unit == "元" else claim.unit)

    matched_entry = next(((v, b) for v, b in candidates if abs(claim.value - v) <= tol), None)
    if matched_entry is not None:
        ref_value, used_basis = matched_entry
        result.ref_basis = {"flow": {"cumulative": "累计值", "single_quarter": "单季值"},
                            "point": {"cumulative": "期末时点值", "single_quarter": "期末时点值"},
                            "ratio": {"cumulative": "累计口径", "single_quarter": "单季口径"}}[kind][used_basis]
        result.reference_display = _fmt_value(ref_value, unit)
        # 数值一致 → 口径一致性检查
        if claim.period_basis == "quarter" and used_basis == "cumulative":
            # quarter 口径限定单季候选，理论上不会走到这里；防御性保留
            single = snap.value(claim.metric, claim.period, "single_quarter")
            if single is not None and abs(claim.value - single) > tol:
                result.status = "fail"
                result.diff_reason = ("声明为单季，数值却与累计值一致"
                                      f"（单季参考 {_fmt_value(single, unit)}），疑似将累计误作单季")
                result.suggestion = f"单季值应为 {_fmt_value(single, unit)}（{claim.period}）"
                return result
        if claim.period_basis == "quarter_report" and kind == "flow" and len(candidates) > 1:
            # 歧义期间：两种口径参考均存在且数值与其中之一一致 → needs_review
            both = "；".join(
                f"{'累计' if b == 'cumulative' else '单季'}参考 {_fmt_value(v, unit)}"
                for v, b in candidates)
            result.status = "needs_review"
            result.diff_reason = (f"期间标签 {claim.period} 未注明单季/累计口径，"
                                  f"数值与{result.ref_basis}一致，但两种读法均有可能")
            result.limitations.append(f"{both}；建议报告明确标注口径后复核")
            return result
        result.status = "pass"
        result.basis_detail = f"独立重算参考 {_fmt_value(ref_value, unit)}（{result.ref_basis}）"
        _evidence_check(result, claim, snap, snapshot_values)
        return result

    result.status = "fail"
    result.difference = f"声称 {claim.display} vs 参考 {result.reference_display}"
    # 错误归因提示
    conversion_hint = _unit_conversion_hint(claim.value, ref_value)
    if conversion_hint:
        result.diff_reason = conversion_hint
    elif claim.value * ref_value < 0 and abs(abs(claim.value) - abs(ref_value)) <= tol:
        result.diff_reason = "正负号相反"
    elif claim.period_basis == "quarter":
        cum_v = snap.value(claim.metric, claim.period, "cumulative")
        if cum_v is not None and abs(claim.value - cum_v) <= tol:
            result.diff_reason = (f"数值与累计值一致（{_fmt_value(cum_v, unit)}），"
                                  "疑似将累计误作单季")
        else:
            result.diff_reason = "数值与独立重算参考值不符"
    else:
        other = _find_other_metric_with_value(snap, claim, ref_value, tol)
        if other:
            result.diff_reason = f"数值与「{other}」的参考值一致，疑似指标名称误用"
        else:
            result.diff_reason = "数值与独立重算参考值不符"
    result.suggestion = f"建议改为 {result.reference_display}（{claim.metric_raw}，{claim.period}，{result.ref_basis}）"
    _snapshot_check(result, claim, snapshot_values, snap)
    _evidence_check(result, claim, snap, snapshot_values)
    return result


def _find_other_metric_with_value(snap: ReferenceSnapshot, claim: Claim,
                                  ref_value: float, tol: float) -> str | None:
    """声称值是否与其他指标的参考值完全一致（指标误用提示）。"""
    cn = {"revenue": "营业收入", "net_profit": "净利润", "parent_net_profit": "归母净利润",
          "operating_cash_flow": "经营现金流净额", "total_assets": "总资产",
          "total_liabilities": "总负债", "accounts_receivable": "应收账款", "inventory": "存货"}
    for metric, name in cn.items():
        if metric == claim.metric:
            continue
        v = snap.value(metric, claim.period, "cumulative")
        if v is not None and abs(claim.value - v) <= tol:
            return name
    return None


# ---------------- V4：证据引用核验 ----------------

def _evidence_check(result: VerificationResult, claim: Claim, snap: ReferenceSnapshot,
                    snapshot_values: dict | None) -> None:
    """引用存在性、公司归属、期间、字段一致性检查（发现错误改判 fail）。"""
    if not claim.evidence_ref:
        return
    eid = claim.evidence_ref
    if snapshot_values is None:
        result.limitations.append(f"引用 {eid}：无证据登记表可比对，仅完成数值核验")
        return
    entry = snapshot_values.get(eid)
    if entry is None:
        result.status = "fail"
        result.diff_reason = f"引用的证据 ID {eid} 不存在"
        result.suggestion = "修正或删除该引用"
        return
    ev_code, ev_period, ev_metric, ev_value = entry
    if ev_code and snap.code and ev_code != snap.code:
        result.status = "fail"
        result.diff_reason = f"引用 {eid} 属于公司 {ev_code}，与核验对象 {snap.code} 不符（不得串用）"
        result.suggestion = f"替换为 {snap.code} 的对应证据"
        return
    if ev_period and claim.period and ev_period != claim.period:
        result.status = "fail"
        result.diff_reason = f"引用 {eid} 对应期间 {ev_period}，与声明期间 {claim.period} 不符"
        result.suggestion = f"改用 {claim.period} 的证据"
        return
    if ev_metric and claim.metric and ev_metric != claim.metric:
        result.status = "fail"
        result.diff_reason = (f"引用 {eid} 对应字段「{ev_metric}」，与陈述的"
                              f"「{claim.metric_raw}」不一致")
        result.suggestion = "改引对应指标的证据"
        return
    result.limitations.append(f"引用 {eid} 结构校验一致（存在/公司/期间/字段）")


def _snapshot_check(result: VerificationResult, claim: Claim,
                    snapshot_values: dict | None, snap: ReferenceSnapshot) -> None:
    """报告生成时证据值与当前参考不一致 → 标注快照差异限制，不判错误。"""
    if snapshot_values is None or claim.value is None:
        return
    entries = []
    if claim.evidence_ref:
        entry = snapshot_values.get(claim.evidence_ref)
        if entry:
            entries.append(entry)
    else:  # 无引用时按（指标, 期间）回退匹配报告生成时的登记值
        entries = [v for v in snapshot_values.values()
                   if v[2] == claim.metric and claim.period and v[1] == claim.period]
    tol = _tolerance(claim, "元" if claim.unit == "元" else claim.unit)
    for _code, _period, _metric, ev_value in entries:
        if ev_value is None:
            continue
        if abs(claim.value - ev_value) <= tol:
            result.status = "unable_to_verify"
            result.diff_reason = ("报告数值与其生成时证据一致，但与当前独立参考数据不符"
                                  "——可能因财报修订或数据快照变化，无法判定报告生成时是否有误")
            result.limitations.append("新旧快照差异不直接判为报告错误，需人工核对数据版本")
            return


# ---------------- V5：定性结论与状态词 ----------------

def verify_status_word(claim: Claim, snap: ReferenceSnapshot) -> VerificationResult:
    """同比特殊状态词声明（扭亏为盈/由盈转亏/亏损收窄/亏损扩大）。"""
    from finsight.verify.claims import _STATUS_WORDS
    result = VerificationResult(cid=claim.cid, status="unable_to_verify",
                                claim_text=claim.text, location=claim.location,
                                claimed_display=claim.display)
    word_status = next((s for w, s in _STATUS_WORDS.items() if w in claim.display), None)
    if word_status is None or claim.period is None:
        result.diff_reason = "状态词声明缺少期间或无法映射状态"
        return result
    entry = snap.yoy.get((claim.metric, claim.period))
    if not entry:
        result.status = "insufficient_evidence"
        result.diff_reason = f"参考数据无 {claim.period} 的同比数据"
        return result
    ref_value, ref_status = entry
    result.ref_period, result.ref_basis = claim.period, "累计同比"
    result.reference_display = f"{ref_value * 100:+.2f}%（状态：{ref_status}）"
    if ref_status == word_status:
        result.status = "pass"
        result.basis_detail = f"参考同比状态分类为 {ref_status}，与声明一致"
        return result
    result.status = "fail"
    result.diff_reason = f"参考同比状态为 {ref_status}，与声明「{claim.display}」不符"
    result.suggestion = f"建议按参考值改写（{result.reference_display}）"
    return result


def verify_qualitative(claim: Claim) -> VerificationResult:
    """定性断言：保守处理，不因引用存在或关键词匹配而判通过。"""
    result = VerificationResult(cid=claim.cid, status="needs_review",
                                claim_text=claim.text, location=claim.location,
                                claimed_display="（定性结论）")
    if claim.evidence_ref:
        result.diff_reason = ("定性断言附有引用，但语义支持关系（引用是否足以支撑结论）"
                              "需人工判断；程序仅确认引用存在")
        result.limitations.append("关键词匹配不作为语义核验通过依据")
    else:
        result.status = "insufficient_evidence"
        result.diff_reason = "定性断言未附任何证据引用"
        result.suggestion = "补充可核验的财务/经营证据引用，或标注为主观判断"
    return result


# ---------------- V4 补充：来源描述检查 ----------------

_PDF_PAGE_PATTERN = re.compile(r"(公告|年报|年报原文|定期报告)[^。；]{0,12}第\s*\d+\s*页")


def check_source_descriptions(fragments_text: str, findings: list[str]) -> None:
    """检测把结构化数据来源错误描述为公告 PDF 页码引用。"""
    for m in _PDF_PAGE_PATTERN.finditer(fragments_text):
        findings.append(
            f"来源描述存疑：「{m.group(0)}」——本项目数据来源为东方财富结构化财务数据，"
            "不存在公告 PDF 页码引用；若报告声称引用公告原文页码，属于来源描述错误")


# ---------------- 主入口 ----------------

def verify_claim_set(claim_set: ClaimSet, snap: ReferenceSnapshot,
                     snapshot_values: dict | None = None,
                     fragments_text: str = "",
                     target_desc: str = "") -> VerificationReportData:
    """执行全部核验规则。snapshot_values 为报告生成时的证据登记（可空）。"""
    report = VerificationReportData(target_desc=target_desc, code=snap.code,
                                    claim_count=len(claim_set.claims))
    if snapshot_values is not None:
        report.snapshot_note = "已提供报告生成时的证据登记，用于快照差异检测与引用校验"
    for claim in claim_set.claims:
        if claim.kind == "numeric":
            report.results.append(verify_numeric(claim, snap, snapshot_values))
        elif claim.kind == "status_word":
            report.results.append(verify_status_word(claim, snap))
        else:
            report.results.append(verify_qualitative(claim))
    if fragments_text:
        check_source_descriptions(fragments_text, report.source_findings)
    return report
