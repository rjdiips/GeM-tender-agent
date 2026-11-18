"""Checks that a quoted passage really is in the cited page (R5). A model that paraphrases or invents a quote
does not get its evidence accepted."""
from __future__ import annotations

import re
from difflib import SequenceMatcher

MIN_WORDS = 4


def _norm(text: str) -> str:
    return " ".join(re.findall(r"\w+", text.lower()))


# where the model joined passages: "...", "…", "[...]", or a spaced slash ("Item Category: Dinner Set (V2) (Q4) /
# Total Quantity: 367", 8271159). A real " / " in the text ("Ministry of Finance / Department …") still verifies, as
# both sides are on the page.
_GAP = re.compile(r"\.\.\.+|…|\[\.\.\.\]|\s/\s")


def too_short(quote: str) -> bool:
    """A quote under MIN_WORDS words ("EMD Amount 50000") proves too little to count, even when it is there."""
    return len(_norm(quote).split()) < MIN_WORDS


def verify_quote(quote: str, page_text: str, min_ratio: float = 0.85) -> bool:
    """Is the quote on the page, word for word (spacing, case and punctuation aside)? A quote the model stitched
    together with "..." is checked part by part, in any order: GeM's bid PDF puts each field's label and value on
    separate lines between Hindi ones, and 8251705's "Item Category: Revolving Chair (V5) (Q2) ... Total Quantity: 29"
    cited two real fields in the opposite order to the page (17 Nov)."""
    if too_short(quote):
        return False
    page = _norm(page_text)
    parts = [p for p in (_norm(part) for part in _GAP.split(quote)) if p]
    if len(parts) > 1:
        # a short part must be there exactly; a longer one may be a near match, as a whole quote may
        return all(part in page if len(part.split()) < MIN_WORDS else _found(part, page, min_ratio) for part in parts)
    return _found(_norm(quote), page, min_ratio)


def _found(q: str, page: str, min_ratio: float) -> bool:
    if q in page:
        return True
    matcher = SequenceMatcher(None, q, page, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks() if block.size >= 6)
    return matched / len(q) >= min_ratio
