"""Text of any downloaded file, by kind: PDF (with OCR fallback), HTML, docx, xlsx."""
from __future__ import annotations

import io
import zipfile

from gem_agent.tools.office import docx_text, html_text, xlsx_text
from gem_agent.tools.pdf.text import OcrFn, PageText, extract_pdf


def office_type(content: bytes) -> str | None:
    """docx | xlsx | None (pptx and others are not read)."""
    try:
        names = set(zipfile.ZipFile(io.BytesIO(content)).namelist())
    except zipfile.BadZipFile:
        return None
    if "word/document.xml" in names:
        return "docx"
    if "xl/workbook.xml" in names:
        return "xlsx"
    return None


def extract_text(content: bytes, kind: str, ocr: OcrFn | None) -> list[PageText] | None:
    """Pages of text, or None when the kind can't be read."""
    if kind == "pdf":
        return extract_pdf(content, ocr)
    if kind == "html":
        return html_text(content)
    if kind == "office":
        sub = office_type(content)
        return docx_text(content) if sub == "docx" else xlsx_text(content) if sub == "xlsx" else None
    return None


def suffix_for(kind: str, filename: str, content: bytes) -> str:
    if kind == "office":
        return "." + (office_type(content) or "bin")
    return {"pdf": ".pdf", "html": ".html", "archive": ".zip"}.get(kind) or (
        "." + filename.rsplit(".", 1)[-1][:8] if "." in filename else ".bin")
