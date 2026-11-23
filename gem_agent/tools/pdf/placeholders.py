"""Placeholder attachment detection by content.

GeM buyers often upload one-line stand-ins ("As per Buyer ATC", "Please refer the RFP uploaded in …"). In the
199-file survey every placeholder was one page with at most 70 characters of text; the smallest real document
had 732. A file with no text at all is not a placeholder: it may be a scan that OCR could not read.
"""
from __future__ import annotations

from gem_agent.tools.pdf.text import PageText, text_chars

MAX_PLACEHOLDER_CHARS = 200


def is_placeholder(pages: list[PageText], max_chars: int = MAX_PLACEHOLDER_CHARS) -> bool:
    if len(pages) != 1:
        return False
    chars = text_chars(pages[0].text)
    return 0 < chars < max_chars
