"""报告文本提取：Markdown / DOCX → 带位置信息的文本片段。

- 报告正文属于不可信输入：仅做正则提取与统计，绝不执行其中内容。
- 文件校验：扩展名白名单（.md/.docx）、大小上限 10MB、损坏文件抛出
  可读异常。
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
ALLOWED_EXTENSIONS = (".md", ".docx")


@dataclass
class Fragment:
    """一个可定位的文本片段（段落/表格单元格）。"""

    text: str
    location: str      # 如 "段落 3" / "标题 2" / "表格 2 行 3 列 1"
    section: str = ""  # 所属章节标题（如有，来自最近的前置标题）


class DocumentError(Exception):
    """文件格式/损坏/超限等输入异常。"""


def validate_upload(filename: str, data: bytes) -> None:
    """上传文件的基本校验：扩展名、大小、非空。"""
    if not filename or not filename.lower().endswith(ALLOWED_EXTENSIONS):
        raise DocumentError(f"仅支持 {'/'.join(ALLOWED_EXTENSIONS)} 文件，收到：{filename or '空文件名'}")
    if not data:
        raise DocumentError("文件内容为空")
    if len(data) > MAX_FILE_SIZE:
        raise DocumentError(f"文件超过大小上限（{MAX_FILE_SIZE // (1024 * 1024)}MB）")


def extract_fragments(filename: str, data: bytes) -> list[Fragment]:
    """按文件类型提取文本片段（带位置）。"""
    validate_upload(filename, data)
    if filename.lower().endswith(".md"):
        return extract_markdown(data.decode("utf-8", errors="replace"))
    return extract_docx(data)


def extract_markdown(text: str) -> list[Fragment]:
    """Markdown → 片段（按行；记录标题层级用于 section 定位）。"""
    fragments: list[Fragment] = []
    section = ""
    for i, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            section = heading.group(2).strip()
            fragments.append(Fragment(stripped, f"第 {i} 行（标题）", section=section))
        elif stripped.startswith("|"):  # markdown 表格行
            fragments.append(Fragment(stripped, f"第 {i} 行（表格）", section=section))
        elif stripped.startswith("- ") or stripped.startswith(">"):
            fragments.append(Fragment(stripped.lstrip("> "), f"第 {i} 行", section=section))
        else:
            fragments.append(Fragment(stripped, f"第 {i} 行", section=section))
    return fragments


def extract_docx(data: bytes) -> list[Fragment]:
    """DOCX → 片段（正文段落 + 表格单元格，位置精确到行列）。"""
    from docx import Document

    try:
        doc = Document(io.BytesIO(data))
    except Exception as exc:
        raise DocumentError(f"DOCX 文件无法解析（可能已损坏）：{type(exc).__name__}") from None

    fragments: list[Fragment] = []
    section = ""
    for i, para in enumerate(doc.paragraphs, start=1):
        text = para.text.strip()
        if not text:
            continue
        if para.style.name.startswith("Heading") or para.style.name.startswith("Title"):
            section = text
            fragments.append(Fragment(text, f"段落 {i}（标题）", section=section))
        else:
            fragments.append(Fragment(text, f"段落 {i}", section=section))
    for t_idx, table in enumerate(doc.tables, start=1):
        for r_idx, row in enumerate(table.rows, start=1):
            for c_idx, cell in enumerate(row.cells, start=1):
                text = cell.text.strip()
                if text:
                    fragments.append(Fragment(text, f"表格 {t_idx} 行 {r_idx} 列 {c_idx}", section=section))
    if not fragments:
        raise DocumentError("文档中未提取到任何文本内容")
    return fragments
