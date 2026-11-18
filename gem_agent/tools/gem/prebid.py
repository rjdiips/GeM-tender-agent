"""GeM's own pre-bid date, read from its bid document (17 Nov).

GeM publishes no pre-bid or queries field in its listing or details JSON. When a buyer sets one, GeM prints it on
the bid PDF it generates, in a fixed block: "Pre Bid Detail(s)", the "Pre-Bid Date and Time" and "Pre-Bid Venue"
labels, then the value as DD-MM-YYYY HH:MM:SS. This is GeM's own template, like its listing JSON, so it is parsed
here. A buyer's own RFP words its queries date freely; the Judge reads that one (agents/judge.py).
"""
from __future__ import annotations

import re
from datetime import datetime

_HEADING = re.compile(r"Pre[\s-]*Bid\s+Detail", re.IGNORECASE)
_VALUE = re.compile(r"(\d{2})-(\d{2})-(\d{4})\s+(\d{2}):(\d{2})(?::(\d{2}))?")
_REACH = 300  # the value follows the two labels (with their Hindi) within ~150 characters


def parse_pre_bid(text: str) -> tuple[datetime, str] | None:
    """The pre-bid date and time, and its line as printed, from the text of a GeM bid document; None if it has none."""
    for heading in _HEADING.finditer(text):
        m = _VALUE.search(text, heading.end(), heading.end() + _REACH)
        if m:
            d, mo, y, h, mi, sec = m.groups()
            try:
                when = datetime(int(y), int(mo), int(d), int(h), int(mi), int(sec or 0))
            except ValueError:
                continue
            return when, f"Pre-Bid Date and Time {m.group(0)}"
    return None
