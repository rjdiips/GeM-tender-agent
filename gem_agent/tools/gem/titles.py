"""Titles that say nothing about the subject (spec 6.2, the vague-title gap)."""
from __future__ import annotations

import re

_VAGUE = re.compile(r"^(hiring of agency for it projects- milestone basis|custom bid for services\s*-[\s-]*)$", re.IGNORECASE)


def is_vague_title(title: str) -> bool:
    """A bare service category: "Hiring of Agency for IT Projects- Milestone basis", or a custom bid with nothing
    after the dash. On 16 Nov these hid the only open digital-lending (LOS-type) and fraud-risk tenders."""
    return bool(_VAGUE.match(" ".join(title.split())))
