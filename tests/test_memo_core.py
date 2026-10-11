"""备忘录证据层与骨架测试：本地构造数据，不联网、不依赖 LLM。"""
import pandas as pd
import pytest

from finsight.finance.field_mapping import REPORT_DATE_FIELD, standardize_statement
from finsight.memo.evidence import build_evidence_book
from finsight.memo.skeleton import DISCLAIMER, SECTION_ORDER, build_memo_skeleton


def qd(year, quarter):
    m, d = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}[quarter]
    return pd.Timestamp(year, m, d)


def cum(singles):
    total, out = 0, []
    for s in singles:
        total += s
        out.append(total)
    return out


Y2324 = [qd(2023, q) for q in (1, 2, 3, 4)] + [qd(2024, q) for q in (1, 2, 3, 4)]


def make_std(kind, dates, cols):
    raw = pd.DataFrame({REPORT_DATE_FIELD: [d.strftime("%Y-%m-%d %H:%M:%S") for d in dates], **cols})
    return standardize_statement(kind, raw)


def sample_frames(with_balance=True, with_cashflow=True):
    inc = make_std("income", Y2324, {
        "OPERATE_INCOME": cum([100] * 4) + cum([120, 110, 90, 95]),
        "OPERATE_COST": cum([60] * 4) + cum([70, 66, 54, 57]),
        "NETPROFIT": cum([50] * 4) + cum([10, -5, -10, -15]),
        "PARENT_NETPROFIT": cum([45] * 4) + cum([9, -4, -9, -13]),
    })
    bal = make_std("balance", Y2324, {
        "TOTAL_ASSETS": [1000] * 8, "TOTAL_LIABILITIES": [600] * 8,
        "TOTAL_CURRENT_ASSETS": [500] * 8, "TOTAL_CURRENT_LIAB": [400] * 8,
        "INVENTORY": [200 + i for i in range(8)],
        "ACCOUNTS_RECE": [100, 100, 100, 100, 110, 150, 120, 130],
        "TOTAL_EQUITY": [400] * 8,
    }) if with_balance else None
    cf = make_std("cashflow", Y2324, {
        "NETCASH_OPERATE": cum([40] * 4) + cum([-10, 30, 40, 50]),
    }) if with_cashflow else None
    return inc, bal, cf


# ---------------- 证据层 ----------------

def test_evidence_values_and_ids():
    inc, bal, cf = sample_frames()
    book = build_evidence_book("SH600519", inc, bal, cf)
    # 目标期 = 2024Q4：核心字段证据存在且数值正确（元）
    assert book.get("E-REV-2024Q4").value == pytest.approx(415)
    assert book.get("E-NP-2024Q4").value == pytest.approx(-20)
    assert book.get("E-OCF-2024Q4").value == pytest.approx(110)
    assert book.get("E-ASSETS-2024Q4").value == pytest.approx(1000)
    # 报告期关联：证据记录正确的 report_date
    assert book.get("E-REV-2024Q4").report_date == "2024-12-31"
    # 比率与同比证据（复用引擎结果）
    assert book.get("E-GM-2024Q4").value == pytest.approx((415 - 247) / 415)
    assert book.get("E-NPYOY-2024Q4").value == pytest.approx((-20 - 200) / 200)
    assert "profit_to_loss" in book.get("E-NPYOY-2024Q4").note  # 特殊状态保留
    # 证据 ID 唯一
    assert len(book.ids()) == len(set(book.ids()))


def test_evidence_period_mismatch_noted():
    # 资产负债表缺 2024Q4 → 实际采用 2024Q3 并明确标注，不默默混合
    inc, bal, cf = sample_frames()
    bal = bal[bal["report_date"] <= pd.Timestamp(2024, 9, 30)]
    book = build_evidence_book("T", inc, bal, cf)
    assert book.get("E-ASSETS-2024Q3") is not None
    assert any("资产负债表实际采用 2024Q3" in m for m in book.missing)


def test_evidence_missing_table_degrades():
    inc, _, _ = sample_frames(with_balance=False, with_cashflow=False)
    book = build_evidence_book("T", inc, None, None)
    assert book.get("E-REV-2024Q4") is not None
    assert book.get("E-ASSETS-2024Q4") is None
    # 风险信号降级：R3/R4 因缺表记为无法评估
    assert any("R3" in m and "现金流量表不可用" in m for m in book.missing)
    assert any("R4" in m and "资产负债表不可用" in m for m in book.missing)


def test_evidence_income_required():
    book = build_evidence_book("T", None, None, None)
    assert not book.evidences
    assert any("利润表不可用" in m for m in book.missing)


def test_evidence_signals_recorded():
    inc, bal, cf = sample_frames()
    book = build_evidence_book("T", inc, bal, cf)
    sig_ids = [e for e in book.ids() if e.startswith("E-SIG-")]
    assert any(i.startswith("E-SIG-R1-") for i in sig_ids)   # +20%→+10% 触发
    assert book.snapshot["signals_total"] == len(sig_ids)


# ---------------- 骨架 ----------------

@pytest.fixture()
def memo():
    inc, bal, cf = sample_frames()
    book = build_evidence_book("SH600519", inc, bal, cf)
    return build_memo_skeleton("SH600519", book)


def test_skeleton_sections_complete(memo):
    assert [s.key for s in memo.sections] == SECTION_ORDER
    assert memo.period == "2024Q4"
    assert memo.llm_status == "未配置"


def test_skeleton_facts_cite_valid_eids(memo):
    import re
    valid = set(memo.evidence.ids())
    cited = set()
    for sec in memo.sections:
        for fact in sec.facts:
            cited.update(re.findall(r"\[(E-[A-Z0-9-]+)\]", fact))
    assert cited and cited <= valid, f"无效引用: {cited - valid}"


def test_skeleton_special_status_wording(memo):
    summary = memo.section("summary")
    text = " ".join(summary.facts)
    # 由盈转亏不得显示为普通增长
    assert "由盈转亏" in text
    financials_text = " ".join(memo.section("financials").facts)
    assert "由盈转亏" in financials_text


def test_skeleton_llm_sections_pending(memo):
    for key in ("company", "thesis", "scenarios"):
        sec = memo.section(key)
        assert sec.kind == "llm" and sec.llm_text is None and sec.status == "llm_pending"


def test_skeleton_open_questions_and_no_fabrication(memo):
    text = " ".join(memo.section("open_questions").facts)
    assert "主营业务" in text and "股价" in text
    # 结论性章节（摘要/投资逻辑）不得出现编造性表述
    # （情景章节中的"不生成目标价"等否定语境免责句不在扫描范围）
    claim_text = " ".join(f for key in ("summary", "thesis")
                          for f in memo.section(key).facts)
    for banned in ("目标价", "预期收益率", "行业排名第一", "竞争优势显著"):
        assert banned not in claim_text


def test_skeleton_risks_from_engine(memo):
    risks_text = " ".join(memo.section("risks").facts)
    assert "R1" in risks_text and "risk_hint" in DISCLAIMER
