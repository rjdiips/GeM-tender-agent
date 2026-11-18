"""Tesseract OCR (English) for pages with almost no text (spec 3.4). Hindi has no language pack installed."""
from __future__ import annotations

import pymupdf
import pytesseract
from PIL import Image


def ocr_page(pdf: bytes, index: int, *, dpi: int = 200, lang: str = "eng") -> str:
    """Render one page (0-based index) at `dpi` and read it with Tesseract."""
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    try:
        pix = doc[index].get_pixmap(dpi=dpi)
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    finally:
        doc.close()
    return pytesseract.image_to_string(image, lang=lang)
