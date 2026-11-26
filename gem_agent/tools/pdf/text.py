"""Text extraction per page (PyMuPDF), with OCR only for pages that have almost no text."""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Literal

import pymupdf
from pydantic import BaseModel

log = logging.getLogger(__name__)

MIN_TEXT_CHARS = 30  # a page with fewer non-space characters is treated as having no text layer

OcrFn = Callable[[bytes, int], str]  # (pdf bytes, 0-based page index) -> text


class PageText(BaseModel):
    page: int  # 1-based
    text: str
    method: Literal["text", "ocr", "none"]


def text_chars(text: str) -> int:
    return len(re.sub(r"\s", "", text))


def extract_pdf(pdf: bytes, ocr: OcrFn | None, min_chars: int = MIN_TEXT_CHARS) -> list[PageText]:
    """One PageText per page. Garbled Hindi from legacy font encodings is kept as is; the documents are
    bilingual and the English text carries the content."""
    try:
        doc = pymupdf.open(stream=pdf, filetype="pdf")
    except (pymupdf.FileDataError, RuntimeError, ValueError) as exc:
        log.warning("unreadable PDF: %s", exc)
        return []
    pages = []
    try:
        for i in range(doc.page_count):
            page = doc[i]
            # what get_text() does for the "text" option, spelled with the typed calls it wraps
            text = page.get_textpage(flags=pymupdf.TEXTFLAGS_TEXT).extractText()
            if text_chars(text) >= min_chars:
                pages.append(PageText(page=i + 1, text=text, method="text"))
            elif ocr is not None:
                pages.append(PageText(page=i + 1, text=ocr(pdf, i), method="ocr"))
            else:
                pages.append(PageText(page=i + 1, text=text, method="none"))
    finally:
        doc.close()
    return pages
