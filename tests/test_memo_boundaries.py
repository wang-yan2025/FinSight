"""备忘录可信度边界测试：程序化校验能力的如实声明与未核验标注。

核心边界（与 llm.py 声明一致）：
- 数字命中全局白名单 ≠ 指标归属/单位/报告期正确；
- 引用合法证据 ID ≠ 证据支持该结论；
- 语义级核验未实现，LLM 内容必须携带统一可信度标注。
"""
import io

import pandas as pd
import pytest
from docx import Document

from finsight.finance.field_mapping import REPORT_DATE_FIELD, standardize_statement
from finsight.memo.evidence import build_evidence_book
from finsight.memo.llm import (
    LLMConfig,
    LLM_PROVENANCE_NOTE,
    apply_llm_sections,
    build_number_allowlist,
    validate_llm_sections,
)
from finsight.memo.render import memo_to_docx_bytes, memo_to_markdown
from finsight.memo.skeleton import DISCLAIMER, build_memo_skeleton


def qd(year, quarter):
    m, d = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}[quarter]
    return pd.Timestamp(year, m, d)


def cum(singles):
    total, out = 0, []
    for s in singles:
        total += s
        out.append(total)
    return out


def _memo_with_llm(llm_summary: str):
    dates = [qd(2023, q) for q in (1, 2, 3, 4)] + [qd(2024, q) for q in (1, 2, 3, 4)]
    inc = standardize_statement("income", pd.DataFrame({
        REPORT_DATE_FIELD: [d.strftime("%Y-%m-%d %H:%M:%S") for d in dates],
        "OPERATE_INCOME": cum([100] * 4) + cum([120, 110, 90, 95]),   # 2024Q4 累计 415
        "OPERATE_COST": cum([60] * 4) + cum([70, 66, 54, 57]),
        "NETPROFIT": cum([50] * 4) + cum([10, -5, -10, -15]),          # 2024Q4 累计 −20
    }))
    book = build_evidence_book("T", inc, None, None)
    memo = build_memo_skeleton("T", book)
    allow = build_number_allowlist(memo)
    valid = set(memo.evidence.ids())
    data = {"summary": llm_summary, "company": "概况待补充。",
            "thesis": "逻辑待补充。", "scenarios": "定性情景。"}
    sections, issues = validate_llm_sections(data, valid, allow)
    apply_llm_sections(memo, sections, issues)
    return memo, sections, issues


def _no_verified_claim(md: str):
    """输出中不得出现暗示已完成事实核验的措辞。"""
    for banned in ("已经程序化校验", "已核实", "事实核验完成", "已经核验"):
        assert banned not in md, f"出现误导性核验措辞：{banned}"


def test_revenue_number_misattributed_not_claimed_verified():
    """模型把收入数字说成净利润：数字在白名单内、引用合法 → 结构校验通过，
    但输出必须携带未核验标注，不得暗示事实已核实。"""
    memo, sections, issues = _memo_with_llm("净利润高达 415 元，盈利强劲 [E-REV-2024Q4]。")
    assert "summary" in sections  # 结构层面确实无法识别张冠李戴
    md = memo_to_markdown(memo)
    assert LLM_PROVENANCE_NOTE in md
    assert "尚未经独立核验" in md
    _no_verified_claim(md)


def test_valid_eid_with_mismatched_period_keeps_unverified_note():
    """引用合法 ID 但描述的期间不对应（2023Q4 vs 证据 2024Q4）：同样不得宣称已核实。"""
    memo, _, _ = _memo_with_llm("2023Q4 营业收入 415 元 [E-REV-2024Q4]，增长强劲。")
    md = memo_to_markdown(memo)
    assert LLM_PROVENANCE_NOTE in md
    _no_verified_claim(md)


def test_unsupported_number_flagged_with_marker():
    memo, _, issues = _memo_with_llm("预计全年营收将达 8888 亿元。")
    assert any("未核验数字" in i for i in issues)
    md = memo_to_markdown(memo)
    assert "【含未经核验数字，待核实】" in md
    assert LLM_PROVENANCE_NOTE in md


def test_provenance_note_consistent_in_markdown_and_docx():
    memo, _, _ = _memo_with_llm("摘要：收入正增长 [E-REV-2024Q4]。")
    md = memo_to_markdown(memo)
    docx_body = "\n".join(p.text for p in Document(io.BytesIO(memo_to_docx_bytes(memo))).paragraphs)
    for output in (md, docx_body):
        assert LLM_PROVENANCE_NOTE in output
        assert DISCLAIMER[:20] in output
        _no_verified_claim(output)


def test_config_repr_masks_api_key():
    cfg = LLMConfig(api_key="sk-super-secret-value", base_url="https://x/v1", model="m")
    r = repr(cfg)
    assert "sk-super-secret-value" not in r
    assert "已设置" in r and "m" in r


def test_period_isolation_between_books():
    """不同目标报告期的证据集相互独立，数值与期间不混用。"""
    dates = [qd(2023, q) for q in (1, 2, 3, 4)] + [qd(2024, q) for q in (1, 2, 3, 4)]
    inc = standardize_statement("income", pd.DataFrame({
        REPORT_DATE_FIELD: [d.strftime("%Y-%m-%d %H:%M:%S") for d in dates],
        "OPERATE_INCOME": cum([100] * 4) + cum([120, 110, 90, 95]),
        "NETPROFIT": cum([50] * 4) + cum([10, -5, -10, -15]),
    }))
    book_q2 = build_evidence_book("T", inc, None, None, target_date=qd(2024, 2))
    book_q4 = build_evidence_book("T", inc, None, None, target_date=qd(2024, 4))
    assert book_q2.get("E-REV-2024Q2").value == pytest.approx(230)
    assert book_q4.get("E-REV-2024Q4").value == pytest.approx(415)
    assert book_q2.get("E-REV-2024Q4") is None      # 期间不串
    assert book_q4.snapshot["target_period"] == "2024Q4"
