"""Fields on a bid document's first page: the buyer's organisation (the listing does not carry it), and the ministry
and department (for a bid that is no longer in the bid cache, where the listing's copy is gone)."""
from __future__ import annotations

import re

_ORG = re.compile(r"Organisation Name\s*\n\s*([^\n]+)", re.IGNORECASE)
# "[^\S\n]*\n" not "\s*": a blank value must not borrow the next line, which is the next field's label
_MINISTRY = re.compile(r"Ministry/State Name[^\S\n]*\n([^\n]*)", re.IGNORECASE)
_DEPARTMENT = re.compile(r"Department Name[^\S\n]*\n([^\n]*)", re.IGNORECASE)


def buyer_org(page_text: str) -> str | None:
    """The buyer organisation ("Union Bank Of India (ubi)"), from the bid document's first page."""
    match = _ORG.search(page_text)
    if not match:
        return None
    name = " ".join(match.group(1).split())
    return name if name and not name.lower().startswith(("buyer email", "hod email")) else None


def _line_after(pattern: re.Pattern[str], page_text: str) -> str | None:
    match = pattern.search(page_text)
    if not match:
        return None
    value = " ".join(match.group(1).split())
    return value if value and not value.endswith(" Name") else None


def ministry(page_text: str) -> str | None:
    """The ministry ("Ministry Of Finance"), from the bid document's first page."""
    return _line_after(_MINISTRY, page_text)


def department(page_text: str) -> str | None:
    """The department ("Department Of Economic Affairs"), from the bid document's first page."""
    return _line_after(_DEPARTMENT, page_text)
