"""备忘录渲染与导出：Markdown 与 Word（python-docx，中文正常显示）。"""
from __future__ import annotations

import io

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt

from finsight.audit import audit_event
from finsight.memo.llm import LLM_PROVENANCE_NOTE
from finsight.memo.skeleton import DISCLAIMER, InvestmentMemo

_LLM_BANNER = f"【{LLM_PROVENANCE_NOTE}】"


def memo_to_markdown(memo: InvestmentMemo) -> str:
    """完整备忘录 → Markdown（含证据引用、快照、待核实与免责声明）。"""
    lines = [
        f"# 买方投资备忘录（研究辅助稿）——{memo.code}",
        "",
        f"- 生成时间：{memo.generated_at}｜目标报告期：{memo.period}",
        f"- LLM 状态：{memo.llm_status}" +
        (f"（{len(memo.llm_issues)} 条校验提示）" if memo.llm_issues else ""),
        "",
    ]
    for sec in memo.sections:
        lines.append(f"## {sec.title}")
        lines.append("")
        if sec.llm_text:
            lines.append(f"> {_LLM_BANNER}")
            lines.append(f"> {sec.llm_text}")
            lines.append("")
        for fact in sec.facts:
            lines.append(fact)
        if sec.key == "appendix" and memo.evidence is not None:
            lines.append("")
            lines.append("| 证据 ID | 内容 | 值 | 单位 | 报告期 | 来源字段/依据 |")
            lines.append("|---|---|---|---|---|---|")
            for eid in memo.evidence.ids():
                e = memo.evidence.get(eid)
                value = "—" if e.value is None else f"{e.value:,.4g}"
                lines.append(f"| {e.eid} | {e.label} | {value} | {e.unit} | "
                             f"{e.report_date} | {e.derivation} |")
        lines.append("")
    lines.append("---")
    lines.append(f"**免责声明**：{DISCLAIMER}")
    lines.append("")
    audit_event("export_memo", format="markdown", target=memo.code,
                period=memo.period, sections=len(memo.sections),
                chars=len("\n".join(lines)))
    return "\n".join(lines)


def _set_cjk_font(doc: Document) -> None:
    """中文正文用宋体、西文 Calibri（Word 中正常渲染中文）。"""
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10.5)
    style.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "宋体")


def memo_to_docx_bytes(memo: InvestmentMemo) -> bytes:
    """完整备忘录 → DOCX 字节流（标题层级、证据表、免责声明）。"""
    doc = Document()
    _set_cjk_font(doc)

    title = doc.add_heading(f"买方投资备忘录（研究辅助稿）——{memo.code}", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    meta = doc.add_paragraph(
        f"生成时间：{memo.generated_at}｜目标报告期：{memo.period}｜LLM 状态：{memo.llm_status}")
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER

    for sec in memo.sections:
        doc.add_heading(sec.title, level=1)
        if sec.llm_text:
            doc.add_paragraph(_LLM_BANNER)
            doc.add_paragraph(sec.llm_text)
        for fact in sec.facts:
            text = fact.strip("*").strip()
            if fact.startswith("**"):  # 组名 → 二级标题
                doc.add_heading(text, level=2)
            else:
                doc.add_paragraph(text, style="List Bullet" if fact.startswith("- ") else None)
        if sec.key == "appendix" and memo.evidence is not None:
            table = doc.add_table(rows=1, cols=6)
            table.style = "Light Grid Accent 1"
            for i, head in enumerate(["证据 ID", "内容", "值", "单位", "报告期", "来源字段/依据"]):
                table.rows[0].cells[i].text = head
            for eid in memo.evidence.ids():
                e = memo.evidence.get(eid)
                row = table.add_row().cells
                value = "—" if e.value is None else f"{e.value:,.4g}"
                row[0].text, row[1].text, row[2].text = e.eid, e.label, value
                row[3].text, row[4].text, row[5].text = e.unit, e.report_date, e.derivation

    doc.add_heading("免责声明", level=1)
    doc.add_paragraph(DISCLAIMER)

    buf = io.BytesIO()
    doc.save(buf)
    data = buf.getvalue()
    audit_event("export_memo", format="docx", target=memo.code,
                period=memo.period, sections=len(memo.sections), bytes=len(data))
    return data
