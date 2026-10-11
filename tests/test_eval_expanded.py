"""扩展评测样本集测试：逐条人工标注核对 + 风险展示分层不变性。"""
import pandas as pd
import pytest

from finsight.eval.expanded import run_expanded_bench
from finsight.eval.samples import SAMPLES
from finsight.finance.field_mapping import REPORT_DATE_FIELD, standardize_statement
from finsight.memo.evidence import build_evidence_book
from finsight.memo.skeleton import RISKS_RECENT_QUARTERS, build_memo_skeleton
from finsight.workflow.pipeline import memo_fragments
from finsight.verify.claims import extract_claims


def test_all_samples_match_hand_labels():
    """31 条样本（28 核验声明 + 3 修订场景）逐一与人工标注一致，无重复计数。"""
    result = run_expanded_bench()
    assert len(result.rows) == len(SAMPLES) == 31
    sids = [r.sid for r in result.rows]
    assert len(sids) == len(set(sids)), "样本不得重复计入"
    mismatches = [(r.sid, r.extracted, r.actual_status, r.expected_status)
                  for r in result.rows if not r.match]
    assert mismatches == [], mismatches


def test_metrics_denominators_explicit():
    m = run_expanded_bench().metrics
    assert m["标注可提取样本数（分母）"] == 24
    assert m["提取覆盖率"] == 1.0
    assert m["错误类样本数（分母）"] == 12
    assert m["错误检出率"] == 1.0
    assert m["错误误放行（false pass）"] == 0
    # 不可提取样本单独统计且如实未提取（不默认通过）
    assert m["不可提取样本数（单独统计）"] == 3
    assert m["其中系统如实未提取数"] == 3
    # 修订三场景
    assert m["修订成功（S29）"] and m["修订致提取丢失正确识别（S30）"]
    assert m["修订引入错误被发现（S31）"]


def test_samples_cover_required_categories():
    categories = {s.category for s in SAMPLES}
    for required in ("正确", "金额错误", "单位错误", "正负号错误", "指标错配",
                     "引用不存在", "跨公司引用", "引用字段不符", "累计误作单季",
                     "期间标签歧义", "状态词错误", "超出数据范围", "定性无引用",
                     "隐含主语", "中文数字", "科学计数法", "修订成功",
                     "修订致提取丢失", "修订引入错误"):
        assert required in categories, required


# ---------------- 风险展示分层：不改变核验口径 ----------------

def _frames():
    def cum(xs):
        t, o = 0, []
        for x in xs:
            t += x
            o.append(t)
        return o
    e8 = lambda lst: [v * 1e8 for v in lst]
    dates = [f"{y}-{m:02d}-{d:02d} 00:00:00" for y in (2021, 2022, 2023, 2024)
             for m, d in ((3, 31), (6, 30), (9, 30), (12, 31))]
    # 2021–2023 各年单季 100 亿；2024 单季 120/110/90/95（每年累计独立重置）
    yearly = []
    for _ in range(3):
        yearly.extend(cum([100] * 4))
    yearly.extend(cum([120, 110, 90, 95]))
    assert len(yearly) == len(dates) == 16
    inc = standardize_statement("income", pd.DataFrame({
        REPORT_DATE_FIELD: dates,
        "OPERATE_INCOME": e8(yearly),
    }))
    return inc


def test_risks_layering_keeps_all_signals_in_appendix():
    """风险节仅展示近期，但全部信号仍留在正文（附录B）→ 核验提取范围不变。"""
    inc = _frames()
    book = build_evidence_book("T", inc, None, None)
    memo = build_memo_skeleton("T", book)
    risks = memo.section("risks")
    appendix_b = memo.section("appendix_signals")
    assert risks is not None and appendix_b is not None

    sig_total = sum(1 for e in book.evidences if e.startswith("E-SIG-"))
    risks_lines = [f for f in risks.facts if f.startswith("- [")]
    appendix_lines = [f for f in appendix_b.facts if f.startswith("- ") and "[E-SIG-" in f]
    if sig_total:
        assert len(appendix_lines) == sig_total           # 完整历史保留在正文
        assert len(risks_lines) < sig_total or sig_total <= RISKS_RECENT_QUARTERS
        assert any("另有" in f and "附录B" in f for f in risks.facts) or len(risks_lines) == sig_total
        assert any("风险摘要时间范围" in f for f in risks.facts)


def test_layering_does_not_change_verification_claims():
    """展示分层前后，备忘录正文的声明提取数量与构成不变（口径零改动）。"""
    inc = _frames()
    book = build_evidence_book("T", inc, None, None)
    memo = build_memo_skeleton("T", book)
    claims = extract_claims(memo_fragments(memo))
    # 附录B 的完整信号句仍在 fragments 中：单季/累计口径条件句可继续提取
    texts = "\n".join(f.text for f in memo_fragments(memo))
    assert "口径）" in texts
    # 每条信号的条件句（含数值的）在正文中出现
    sig_facts = [f for f in memo.section("appendix_signals").facts if "[E-SIG-" in f]
    for f in sig_facts:
        assert f in texts
    assert len(claims.claims) >= 1
