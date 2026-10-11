"""基础效果评测：固定离线样本 + 人工标注预期 + 确定性指标计算。

样本与预期全部人工标注（含已知正确答案），指标由 Python 统计，
不虚构效果。分母与统计范围随结果一并输出。
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field

import pandas as pd

from finsight.finance.field_mapping import REPORT_DATE_FIELD, standardize_statement
from finsight.verify.claims import extract_claims
from finsight.verify.extract import extract_fragments
from finsight.verify.reference import build_reference_snapshot
from finsight.verify.rules import verify_claim_set
from finsight.workflow.revision import generate_proposals

E8 = lambda lst: [v * 1e8 for v in lst]


def _cum(singles):
    total, out = 0, []
    for s in singles:
        total += s
        out.append(total)
    return out


def build_bench_frames():
    """固定手算参考数据（与核验测试同一数据集，量级真实）。

    2023 单季收入/净利 100/50 亿；2024 单季收入 120/110/90/95 → 累计 415 亿；
    净利 10/−5/−10/−15 → 累计 −20 亿；毛利率 FY≈40.48%；收入同比 FY=+3.75%、
    H1=+15%；净利同比 FY=−110%（由盈转亏）；总资产 1000 亿、总负债 600 亿。
    """
    dates = [f"{y}-{m:02d}-{d:02d} 00:00:00"
             for y in (2023, 2024)
             for m, d in ((3, 31), (6, 30), (9, 30), (12, 31))]
    inc = standardize_statement("income", pd.DataFrame({
        REPORT_DATE_FIELD: dates,
        "OPERATE_INCOME": E8(_cum([100] * 4) + _cum([120, 110, 90, 95])),
        "OPERATE_COST": E8(_cum([60] * 4) + _cum([70, 66, 54, 57])),
        "NETPROFIT": E8(_cum([50] * 4) + _cum([10, -5, -10, -15])),
        "PARENT_NETPROFIT": E8(_cum([45] * 4) + _cum([9, -4, -9, -13])),
    }))
    return build_reference_snapshot("BENCH", inc, None, None)


# 评测样本报告（人工标注 9 条可提取声明，其中 3 处明确模拟错误、
# 1 处口径歧义、1 处超范围期间、1 处定性断言、3 处正确事实）
BENCH_REPORT_MD = "\n".join([
    "# 模拟研究报告（评测样本）",
    "2024年营业收入为415亿元。",              # 正确（累计 415）
    "2024年净利润为-20亿元。",                 # 正确（−20）
    "2024年毛利率为40.5%。",                   # 正确（容差内）
    "2024年营业收入为480亿元。",               # 错误1：金额错误（应 415）
    "2024年净利润为415万元。",                 # 错误2：单位换算错误（数值为收入、单位错）
    "2024年净利润同比扭亏为盈。",              # 错误3：状态词错误（实际由盈转亏）
    "2024Q4营业收入同比增长3.75个百分点。",     # pp/% 概念混淆（确定性 fail）
    "2021年营业收入为300亿元。",               # 超出参考数据范围（insufficient）
    "公司拥有行业领先的竞争壁垒。",             # 定性断言（无引用）
])

# 人工标注：可提取声明 = 句中具备「指标词+数值/状态词+单位」结构的句子
ANNOTATED_EXTRACTABLE = 8      # 上表前 8 句（最后一句定性不属数值可提取，单列）
PLANTED_ERRORS = {             # 错误编号 → 预期核验状态与预期修订方向
    "金额错误": {"cid_metric": "revenue", "expect": "fail"},
    "单位换算错误": {"cid_metric": "net_profit", "expect": "fail"},
    "状态词错误": {"cid_metric": "net_profit_yoy", "expect": "fail"},
    "口径歧义+pp混淆": {"cid_metric": "revenue_yoy", "expect": "needs_review"},
}


@dataclass
class BenchResult:
    metrics: dict = dc_field(default_factory=dict)
    detail: list[str] = dc_field(default_factory=list)


def run_bench() -> BenchResult:
    snap = build_bench_frames()
    frags = extract_fragments("bench.md", BENCH_REPORT_MD.encode("utf-8"))
    claim_set = extract_claims(frags)
    report = verify_claim_set(claim_set, snap, fragments_text=BENCH_REPORT_MD,
                              target_desc="评测样本")

    status_by_cid = {r.cid: r for r in report.results}
    metric_of = {c.cid: c for c in claim_set.claims}

    # 1) 提取覆盖率：成功提取的数值/状态声明 / 人工标注可提取数
    numeric_status_claims = [c for c in claim_set.claims if c.kind in ("numeric", "status_word")]
    coverage = len(numeric_status_claims) / ANNOTATED_EXTRACTABLE

    # 2) 错误检出与误放行
    def claim_status_by(metric, kind=None, value=None):
        out = []
        for c in claim_set.claims:
            if c.metric != metric:
                continue
            if kind and c.kind != kind:
                continue
            if value is not None and c.value is not None and abs(c.value - value) > 1:
                continue
            out.append(status_by_cid[c.cid].status)
        return out

    amount_err = claim_status_by("revenue", "numeric", 480e8)
    unit_err = claim_status_by("net_profit", "numeric", 415e4)
    status_err = claim_status_by("net_profit_yoy", "status_word")
    pp_case = claim_status_by("revenue_yoy", "numeric", 3.75)
    oor = claim_status_by("revenue", "numeric", 300e8)
    detected = sum(1 for s in (amount_err + unit_err + status_err) if s == "fail")
    false_pass = sum(1 for s in (amount_err + unit_err + status_err) if s == "pass")

    # 3) 自动修订建议正确性：确定性建议的 new_display 是否与人工预期一致
    from finsight.memo.skeleton import build_memo_skeleton
    from finsight.memo.evidence import build_evidence_book
    # 用样本报告改造为一个"伪备忘录"结构以复用修订引擎：直接以文本事实构造
    proposals = _bench_proposals(report, claim_set)
    expected_fix = {  # 人工预期（独立于被测函数）
        "金额错误→415亿元": ("480亿元", "415亿元"),
        # 415 万元实为收入数值：按净利润参考值（−20 亿 = −200,000 万元）重写为原单位
        "指标错配→-200,000万元": ("415万元", "-200,000万元"),
    }
    auto_proposals = [p for p in proposals if p.deterministic]
    correct_auto = 0
    for p in auto_proposals:
        for name, (old, new) in expected_fix.items():
            if p.old_display == old and p.new_display == new:
                correct_auto += 1
    auto_precision = correct_auto / len(auto_proposals) if auto_proposals else None

    # 4) 受控修订后真实修复：仅采纳确定性金额/单位建议 → 重新核验
    fixed_real, new_errors = None, None
    revised_md = None
    if auto_proposals:
        accepted = [p.pid for p in auto_proposals
                    if p.kind in ("numeric_fix", "unit_fix")]
        revised_md = BENCH_REPORT_MD
        child_claims = claim_set
        child_report = report
        for pid in accepted:
            p = next(x for x in proposals if x.pid == pid)
            revised_md = revised_md.replace(p.original_sentence, p.new_sentence, 1)
        frags2 = extract_fragments("bench2.md", revised_md.encode("utf-8"))
        child_claims = extract_claims(frags2)
        child_report = verify_claim_set(child_claims, snap, target_desc="修订稿")
        s2 = {r.cid: r.status for r in child_report.results}
        fixed_real = sum(1 for c in child_claims.claims
                         if c.metric == "revenue" and c.value and abs(c.value - 415e8) < 1e6
                         and s2.get(c.cid) == "pass")
        fixed_real += sum(1 for c in child_claims.claims
                          if c.metric == "net_profit" and c.value and abs(c.value - 415e8) < 1e6
                          and s2.get(c.cid) == "pass")
        new_errors = sum(1 for s in s2.values() if s == "fail")

    # 5) 人工复核比例
    review = sum(1 for r in report.results
                 if r.status in ("needs_review", "insufficient_evidence",
                                 "unable_to_verify"))
    review_ratio = review / len(report.results) if report.results else None

    metrics = {
        "标注可提取声明数（分母）": ANNOTATED_EXTRACTABLE,
        "成功提取声明数": len(numeric_status_claims),
        "提取覆盖率": round(coverage, 4),
        "植入错误数": 3,
        "错误检出数": detected,
        "错误检出率": round(detected / 3, 4),
        "错误误放行（false pass）": false_pass,
        "确定性修订建议数": len(auto_proposals),
        "建议正确数（按人工预期核对）": correct_auto,
        "建议正确率": round(auto_precision, 4) if auto_precision is not None else None,
        "受控修订后真实修复数": fixed_real,
        "修订稿再核验 fail 数": new_errors,
        "需人工复核声明数": review,
        "人工复核比例": round(review_ratio, 4) if review_ratio is not None else None,
    }
    detail = [f"{c.cid} {c.kind} {c.metric} {c.display} -> {status_by_cid[c.cid].status}"
              for c in claim_set.claims]
    return BenchResult(metrics=metrics, detail=detail)


def _bench_proposals(report, claim_set):
    """用样本句子直接驱动修订引擎（构造最小 memo 结构）。"""
    from finsight.memo.skeleton import InvestmentMemo, MemoSection
    from finsight.workflow.models import RoundResult

    memo = InvestmentMemo(code="BENCH", period="2024Q4", generated_at="-")
    for line in BENCH_REPORT_MD.splitlines():
        if line.strip() and not line.startswith("#"):
            memo.sections.append(MemoSection("financials", "三、财务表现", "deterministic",
                                             facts=[line]))
    memo.evidence = None
    round_result = RoundResult(round_id=1, vid="V1", report=report,
                               claims=claim_set, fingerprint="")
    return generate_proposals(round_result, None, memo)
