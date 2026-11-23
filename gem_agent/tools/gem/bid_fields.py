"""Fields on a bid document's first page that GeM's listing does not carry: the buyer's organisation."""
from __future__ import annotations

import re

_ORG = re.compile(r"Organisation Name\s*\n\s*([^\n]+)", re.IGNORECASE)


def buyer_org(page_text: str) -> str | None:
    """The buyer organisation ("Union Bank Of India (ubi)"), from the bid document's first page."""
    match = _ORG.search(page_text)
    if not match:
        return None
    name = " ".join(match.group(1).split())
    return name if name and not name.lower().startswith(("buyer email", "hod email")) else None
