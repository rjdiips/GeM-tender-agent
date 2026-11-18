"""Corrigendum flag and text (spec 3.3)."""
from __future__ import annotations

import json
import re
from datetime import datetime
from html.parser import HTMLParser

from pydantic import BaseModel

from gem_agent.tools.http_client import BidplusSession

_MODIFIED_RE = re.compile(r"Modified On:\s*(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)")


class OtherDetails(BaseModel):
    corrigendum: bool
    representation: bool


class CorrigendumEntry(BaseModel):
    modified_on: datetime | None
    changes: list[tuple[str, str]]  # (label, value), e.g. ("Bid extended to", "2026-11-23 16:00:00")


class Corrigendum(BaseModel):
    b_id: int
    entries: list[CorrigendumEntry]

    @property
    def extended_to(self) -> datetime | None:
        """Closing date from the latest "Bid extended to" change."""
        ordered = sorted(self.entries, key=lambda e: e.modified_on or datetime.min)
        latest = None
        for entry in ordered:
            for label, value in entry.changes:
                if label.lower().startswith("bid extended to"):
                    latest = datetime.fromisoformat(value)
        return latest


async def other_details(session: BidplusSession, b_id: int) -> OtherDetails:
    """The cheap check: does the bid have a corrigendum (or representation)?"""
    resp = await session.post_form(f"public-bid-other-details/{b_id}", {})
    resp.raise_for_status()
    body = json.loads(resp.text)["response"]
    return OtherDetails(corrigendum=bool(body.get("corrigendum")), representation=bool(body.get("representation")))


async def view_corrigendum(session: BidplusSession, b_id: int) -> Corrigendum | None:
    """The changes; None when the site answers "0" (no corrigendum)."""
    resp = await session.post_form(f"bidding/bid/viewCorrigendum/{b_id}", {})
    resp.raise_for_status()
    if resp.text.strip() in ("", "0"):
        return None
    return parse_corrigendum(b_id, resp.text)


class _WellParser(HTMLParser):
    """Collects the text of each col-block inside each div.well, with the <strong> part separately."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.wells: list[list[tuple[str, str]]] = []
        self.block: tuple[list[str], list[str]] | None = None
        self.strong = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "div":
            classes = (dict(attrs).get("class") or "").split()
            kind = "well" if "well" in classes else "block" if "col-block" in classes else ""
            self.stack.append(kind)
            if kind == "well":
                self.wells.append([])
            elif kind == "block" and self.wells and "well" in self.stack[:-1]:
                self.block = ([], [])
        elif tag == "strong":
            self.strong += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "strong":
            self.strong = max(0, self.strong - 1)
            return
        closed = self.stack.pop() if tag == "div" and self.stack else None
        if closed == "block" and self.block is not None:
            text = " ".join("".join(self.block[0]).split())
            strong = " ".join("".join(self.block[1]).split())
            self.wells[-1].append((text, strong))
            self.block = None

    def handle_data(self, data: str) -> None:
        if self.block is not None:
            self.block[0].append(data)
            if self.strong:
                self.block[1].append(data)


def parse_corrigendum(b_id: int, html: str) -> Corrigendum:
    parser = _WellParser()
    parser.feed(html)
    entries: list[CorrigendumEntry] = []
    for well in parser.wells:
        for text, strong in well:
            if not text:
                continue
            modified = _MODIFIED_RE.search(text)
            if modified:
                entries.append(CorrigendumEntry(modified_on=datetime.fromisoformat(modified.group(1)), changes=[]))
                continue
            if not entries:
                entries.append(CorrigendumEntry(modified_on=None, changes=[]))
            label = text.replace(strong, "").strip().rstrip(":").strip() if strong else text
            entries[-1].changes.append((label, strong))
    return Corrigendum(b_id=b_id, entries=entries)
