"""扩展评测执行器：对人工标注样本逐条运行并统计。

样本口径（共 31 条，与 samples.py 一致，无重复计数）：
- 28 条核验声明样本（S01–S28）：进入主循环逐条核验；
- 3 条修订场景样本（S29–S31）：仅在修订子实验中执行（不参与主循环，
  避免同一场景重复计入一致率分母）。

统计口径（分母显式声明）：
- 提取覆盖率 = 系统成功提取的样本数 / 人工标注可提取样本数（24 = S01–S24，
  全部预期可提取；S26 复合句按部分提取计 1——只要求提取出首个可核验声明）。
- 不可提取样本（S25/S27/S28，3 条）单独统计为"提取覆盖缺口"，不进入
  错误检出率分母，也不默认视为通过。
- 错误类样本（预期 fail 的 12 条：S07–S17、S19）→ 检出率/误放行。
- 修订子实验（S29–S31）单独统计修复/提取丢失/回归。
- 标注一致率分母 = 31（28 + 3，每条样本恰计一次）。
所有预期值由 samples.py 人工标注，独立于待测实现；结果属固定小样本
统计，不代表任意研究报告的普遍准确率。
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field

import pandas as pd

from finsight.eval.samples import (
    ANNOTATED_EXTRACTABLE_MAIN,
    ANNOTATED_NOT_EXTRACTABLE,
    EVIDENCE_REGISTRY,
    SAMPLES,
)
from finsight.finance.field_mapping import REPORT_DATE_FIELD, standardize_statement
from finsight.verify.claims import ClaimSet, extract_claims
from finsight.verify.extract import extract_fragments
from finsight.verify.reference import build_reference_snapshot
from finsight.verify.rules import VerificationReportData, verify_claim_set

E8 = lambda lst: [v * 1e8 for v in lst]


def _cum(xs):
    t, o = 0, []
    for x in xs:
        t += x
        o.append(t)
    return o


def build_full_frames():
    dates = [f"{y}-{m:02d}-{d:02d} 00:00:00" for y in (2023, 2024)
             for m, d in ((3, 31), (6, 30), (9, 30), (12, 31))]
    inc = standardize_statement("income", pd.DataFrame({
        REPORT_DATE_FIELD: dates,
        "OPERATE_INCOME": E8(_cum([100] * 4) + _cum([120, 110, 90, 95])),
        "OPERATE_COST": E8(_cum([60] * 4) + _cum([70, 66, 54, 57])),
        "NETPROFIT": E8(_cum([50] * 4) + _cum([10, -5, -10, -15])),
        "PARENT_NETPROFIT": E8(_cum([45] * 4) + _cum([9, -4, -9, -13])),
    }))
    bal = standardize_statement("balance", pd.DataFrame({
        REPORT_DATE_FIELD: dates,
        "TOTAL_ASSETS": E8([1000] * 8), "TOTAL_LIABILITIES": E8([600] * 8),
        "TOTAL_CURRENT_ASSETS": E8([500] * 8), "TOTAL_CURRENT_LIAB": E8([400] * 8),
        "INVENTORY": E8([200 + i for i in range(8)]),
        "ACCOUNTS_RECE": E8([100, 100, 100, 100, 110, 150, 120, 130]),
        "TOTAL_EQUITY": E8([400] * 8),
    }))
    cf = standardize_statement("cashflow", pd.DataFrame({
        REPORT_DATE_FIELD: dates,
        "NETCASH_OPERATE": E8(_cum([40] * 4) + _cum([-10, 30, 40, 50])),
    }))
    return inc, bal, cf


def build_income_only_frames():
    inc, _, _ = build_full_frames()
    return inc, None, None


@dataclass
class SampleResult:
    sid: str
    category: str
    extracted: bool
    actual_status: str | None
    expected_status: str | None
    match: bool                 # 提取行为与状态均与标注一致


@dataclass
class ExpandedBenchResult:
    rows: list[SampleResult] = dc_field(default_factory=list)
    metrics: dict = dc_field(default_factory=dict)

    def by_sid(self, sid: str) -> SampleResult:
        return next(r for r in self.rows if r.sid == sid)


def _run_one(sample_text: str, snap, with_evidence: bool):
    frags = extract_fragments("s.md", sample_text.encode("utf-8"))
    claim_set = extract_claims(frags)
    report = verify_claim_set(
        claim_set, snap,
        snapshot_values=EVIDENCE_REGISTRY if with_evidence else None,
        target_desc="样本")
    return claim_set, report


def _find_status(claim_set: ClaimSet, report: VerificationReportData, key) -> tuple[bool, str | None]:
    """按人工标注键定位声明并返回（是否提取到, 状态）。"""
    metric, period, display = key
    status_by_cid = {r.cid: r.status for r in report.results}
    for c in claim_set.claims:
        if metric is None:
            if c.kind == "qualitative":
                return True, status_by_cid.get(c.cid)
            continue
        if (c.metric == metric and (period is None or c.period == period)
                and (display is None or c.display == display)):
            return True, status_by_cid.get(c.cid)
    return False, None


def run_expanded_bench() -> ExpandedBenchResult:
    inc, bal, cf = build_full_frames()
    snap_full = build_reference_snapshot("BENCH", inc, bal, cf)
    inc_only, _, _ = build_income_only_frames()
    snap_income = build_reference_snapshot("BENCH", inc_only, None, None)

    rows: list[SampleResult] = []
    revision_sids = {"S29", "S30", "S31"}   # 修订场景仅在子实验执行一次
    for s in SAMPLES:
        if s.sid in revision_sids:
            continue
        snap = snap_income if s.sid == "S22" else snap_full
        claim_set, report = _run_one(s.text, snap, with_evidence=True)
        is_qualitative = s.category.startswith("定性")
        if s.key is None or s.key[0] is None:
            if is_qualitative:
                extracted = any(c.kind == "qualitative" for c in claim_set.claims)
                actual = None
                if extracted:
                    qcid = next(c.cid for c in claim_set.claims if c.kind == "qualitative")
                    actual = next((r.status for r in report.results if r.cid == qcid), None)
            else:
                # 预期不可提取：以"是否产生任何声明"判定
                extracted = bool(claim_set.claims)
                actual = report.results[0].status if report.results else None
        else:
            extracted, actual = _find_status(claim_set, report, s.key)
        match = (extracted == s.expect_extracted
                 and (s.expect_status is None or actual == s.expect_status))
        rows.append(SampleResult(s.sid, s.category, extracted, actual,
                                 s.expect_status, match))

    # ---- 修订子实验（人工构造编辑，独立标注；S29–S31 每条恰计一次）----
    s07 = "2024年营业收入为480亿元。"
    # S29：确定性修订 480→415 后再核验 → pass
    cs2, rep2 = _run_one("2024年营业收入为415亿元。", snap_full, with_evidence=True)
    ok29, status29 = _find_status(cs2, rep2, ("revenue", "2024Q4", "415亿元"))
    rows.append(SampleResult("S29", "修订成功", ok29, status29, "pass",
                             ok29 and status29 == "pass"))
    # S30：修订删除数值 → 声明不可提取 → coverage_lost（不计修复）
    cs3, rep3 = _run_one("营业收入表现良好。", snap_income, with_evidence=False)
    rows.append(SampleResult("S30", "修订致提取丢失", any(cs3.claims), None, None,
                             not any(cs3.claims)))
    # S31：正确值被改成 480 → 再核验必须 fail（regression 可被发现）
    cs4, rep4 = _run_one(s07, snap_full, with_evidence=True)
    ok31, status31 = _find_status(cs4, rep4, ("revenue", "2024Q4", "480亿元"))
    rows.append(SampleResult("S31", "修订引入错误", ok31, status31, "fail",
                             ok31 and status31 == "fail"))

    # ---- 汇总统计（分母显式）----
    main = [r for r in rows if r.sid in {s.sid for s in SAMPLES[:24]}]
    extracted_main = [r for r in main if r.extracted]
    not_extracted_fixed = [r for r in rows
                           if r.sid in {"S25", "S27", "S28"} and not r.extracted]
    error_samples = [r for r in rows if r.sid in
                     {"S07", "S08", "S09", "S10", "S11", "S12", "S13", "S14",
                      "S15", "S16", "S17", "S19"}]
    detected = [r for r in error_samples if r.actual_status == "fail"]
    false_pass = [r for r in error_samples if r.actual_status == "pass"]
    mismatch = [r for r in rows if not r.match]

    metrics = {
        "标注可提取样本数（分母）": ANNOTATED_EXTRACTABLE_MAIN,
        "成功提取数": len(extracted_main),
        "提取覆盖率": round(len(extracted_main) / ANNOTATED_EXTRACTABLE_MAIN, 4),
        "不可提取样本数（单独统计）": ANNOTATED_NOT_EXTRACTABLE,
        "其中系统如实未提取数": len(not_extracted_fixed),
        "错误类样本数（分母）": len(error_samples),
        "错误检出数（fail）": len(detected),
        "错误检出率": round(len(detected) / len(error_samples), 4),
        "错误误放行（false pass）": len(false_pass),
        "修订成功（S29）": rows[-3].match,
        "修订致提取丢失正确识别（S30）": rows[-2].match,
        "修订引入错误被发现（S31）": rows[-1].match,
        "标注不一致样本": [r.sid for r in mismatch],
        "标注一致率": round(1 - len(mismatch) / len(rows), 4),
    }
    return ExpandedBenchResult(rows=rows, metrics=metrics)
