"""Attachment links from PDF link annotations (pypdf)."""
from __future__ import annotations

import io
import logging

from pypdf import PdfReader
from pypdf.errors import PdfReadError

log = logging.getLogger(__name__)


def pdf_links(pdf: bytes) -> list[tuple[int, str]]:
    """(1-based page, URI) for every URI link annotation, in page order. Unreadable or empty PDFs give []."""
    if not pdf:
        return []
    try:
        reader = PdfReader(io.BytesIO(pdf))
        out: list[tuple[int, str]] = []
        for number, page in enumerate(reader.pages, start=1):
            for annot in page.get("/Annots") or []:
                action = annot.get_object().get("/A")
                uri = action.get_object().get("/URI") if action else None
                if uri:
                    out.append((number, str(uri).strip()))
        return out
    except (PdfReadError, ValueError, KeyError) as exc:
        log.warning("could not read PDF links: %s", exc)
        return []
