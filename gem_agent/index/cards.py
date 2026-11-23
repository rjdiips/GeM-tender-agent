"""Bid cards: what a vague-title bid buys, in one line plus the systems in scope, written by the model from its
documents.

The card joins the indexed text, so every later search finds the bid.

Where the subject is (checked on the target bids): GeM's first pages only repeat the category; the subject is in the
buyer-added terms further into the bid document (page 4-5), or only in the attachment the bid document labels
"Scope of Work: <file>.pdf" — which can itself be a placeholder ("please refer the RFP"); a reverse auction's own
document has neither: its subject is in the original bid. And a bid document can name a broad subject ("Comprehensive
Financial Risk and Compliance Solutions") while only the RFP's scope of work names the modules (AML). So the card
writer collects the attachments (into the document cache, which the Judge reuses) — or, for the bids a run just
collected, reads only what that run left in the document cache, with no GeM request — and reads, in one
model call, the bid document, an RA's original bid, and up to three RFPs (Scope of Work first, then the largest): each by
its first pages, its scope-of-work sections and every line that reads like a title, wherever they sit in a 200-400
page RFP. A card that only restates the title is never stored (the first backfill stored 229 of those): the bid is
marked tried (card '') and stays a vague title that the Searcher always keeps.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import BaseModel, Field, StringConstraints
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.models import Model

from gem_agent.index.db import BidIndex
from gem_agent.tools.gem.models import Bid
from gem_agent.tools.gem.titles import is_vague_title

log = logging.getLogger(__name__)

CARD_INSTRUCTIONS = """You say what an Indian government (GeM) tender is buying, from its bid document and RFP.
The title and "Item Category" are only GeM's generic category ("Hiring of Agency for IT Projects- Milestone basis"):
ignore them. Look for the specific system, software, service or goods in the buyer-added terms, the scope of work,
the RFP and its file names.
subject: name it, and for whom if stated. One line, no preamble.
terms: up to 8 specific systems, modules, functions or goods in scope, as the documents name them, that a search
would use (for example "anti-money laundering (AML)", "transaction monitoring", "enterprise fraud risk management
(EFRM)", "loan origination system"). Never category or procurement words such as "IT projects" or "software".
If the text does not say what is being bought, answer with an empty subject and no terms. Never repeat the category."""

_GENERIC = {"hiring", "of", "agency", "for", "it", "projects", "project", "milestone", "basis", "custom", "bid",
            "services", "service", "software", "development", "the", "and", "a", "an", "solution", "solutions",
            "work", "works", "as", "per", "rfp", "implementation", "procurement", "supply"}

class CardOutput(BaseModel):
    subject: str = Field(max_length=200)
    terms: list[Annotated[str, StringConstraints(max_length=60)]] = Field(max_length=8)


@dataclass
class CardReport:
    written: int = 0
    empty: int = 0
    failed: int = 0


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def is_generic(subject: str, title: str) -> bool:
    """Empty, or only words of the title and generic procurement words."""
    words = _words(subject)
    return not words or words <= (_words(title) | _GENERIC)


def clear_generic_cards(index: BidIndex) -> int:
    """Reset cards whose subject only restates the title, so the card writer tries again."""
    cleared = 0
    for b_id, title, card in index.cards():
        if is_generic(card.split(";")[0], title):
            index.clear_card(b_id)
            cleared += 1
    return cleared


_RFP_LINE = re.compile(r"request for proposal|\brfp\b|tender (?:document|for)|selection of .{3,80} for|scope of work|"
                       r"supply,? (?:installation|implementation)|implementation of", re.IGNORECASE)
# a scope-of-work heading ("3.2 Scope of Work", "ANNEXURE I - Broad Scope of Work", "8. Scope of Work:"), not a
# sentence that mentions it ("Scope of Work is attached vide Annexure-I") nor a contents entry ("Scope of Work .... 15")
# The numbering prefix is atomic (?>…): without it a long run of digits (OCR noise, "3333883233833 3222…" in bid
# 10110777) can be split into section numbers in exponentially many ways, and one line took 13+ min.
_SCOPE_HEADING = re.compile(r"^[(\s]*(?:(?>\d+(?:\.\d+)*\.?|[ivx]+\.|[a-z][.)])\s*|annexure[\s\w-]{0,12}?[-–:(]\s*)*"
                            r"(?:broad\s+|detailed\s+)?scope\s+of\s+(?:work|services|the\s+project)\b"
                            r"(?!\s+(?:is|are|of|as|shall|would|will|also|described|mentioned|defined))[^.]{0,80}$",
                            re.IGNORECASE)
_LABEL = re.compile(r"([A-Z][A-Za-z -]{2,40})\s*:\s*([\w.-]+\.\w{3,4})")


def _scope_sections(pages: list[Any], max_sections: int = 2, chars: int = 2000, skip_pages: int = 0) -> str:
    """The text from each of the first `max_sections` scope-of-work headings (on different pages, after the first
    `skip_pages`), `chars` characters each, running on into the next pages."""
    out: list[str] = []
    after = 0  # the next section starts on a later page
    for i, p in enumerate(pages):
        if i < max(skip_pages, after):
            continue
        offset = 0
        for line in p.text.splitlines(keepends=True):
            bare = line.strip()
            if bare and not re.search(r"\.{4,}|…", bare) and _SCOPE_HEADING.match(bare):
                text = p.text[offset:] + "".join("\n" + q.text for q in pages[i + 1 : i + 6])
                text = re.sub(r"[ \t]+", " ", text)[:chars].strip()
                out.append(f"--- (p.{p.page}) {text}")
                after = i + 1
                break
            offset += len(line)
        if len(out) >= max_sections:
            break
    return "\n".join(out)


def _attachment_text(pages: list[Any], first_pages: int = 3, max_lines: int = 12, max_chars: int = 8000) -> str:
    """An RFP's first pages, its scope-of-work sections, and the lines anywhere in it that read like its title:
    RFPs often open with disclaimers and a contents list, and what is bought can be dozens of pages in."""
    head = "\n".join(p.text for p in pages[:first_pages])[:2000]
    scope = _scope_sections(pages, skip_pages=first_pages)
    lines: list[str] = []
    seen: set[str] = set()  # bare lines: `lines` holds them with a "(p.N)" prefix, so it can't be the duplicate check
    for p in pages[first_pages:]:
        for line in p.text.splitlines():
            line = " ".join(line.split())
            if 20 <= len(line) <= 300 and _RFP_LINE.search(line) and line not in seen:
                seen.add(line)
                lines.append(f"(p.{p.page}) {line}")
                if len(lines) >= max_lines:
                    break
        if len(lines) >= max_lines:
            break
    return (head + (f"\n... scope of work:\n{scope}" if scope else "") +
            ("\n... lines further in:\n" + "\n".join(lines) if lines else ""))[:max_chars]


def _rfp_sections(collector: Any, files: Any, bid_text: str, max_files: int = 3) -> str:
    """Up to `max_files` attachments, the one labelled "Scope of Work" first, then the largest (the RFP); identical
    content uploaded under several labels counts once; placeholders and GeM's general terms are skipped."""
    labels = {name: label.strip().lower() for label, name in _LABEL.findall(bid_text)}
    labels |= {f.filename: f.label.lower() for f in files.files if f.label}  # parsed by the collector (cache/names.py)
    attachments = [f for f in files.files if f.role in ("attachment", "archive_member") and f.status == "ok"]
    attachments.sort(key=lambda f: (labels.get(f.filename) != "scope of work", -f.pages))
    # identical content uploaded under several names counts once, but every name is shown
    groups: dict[int, list[Any]] = {}
    for record in attachments:
        pages = collector.cache.load_text(record)
        groups.setdefault(hash(" ".join(p.text for p in pages)[:5000]), []).append((record, pages))
    sections = []
    for copies in list(groups.values())[:max_files]:
        record, pages = copies[0]
        also = f" (also uploaded as: {', '.join(r.filename for r, _ in copies[1:])})" if len(copies) > 1 else ""
        sections.append(f"=== Attachment {record.filename} ({record.pages} pages){also} ===\n{_attachment_text(pages)}")
    return "\n\n".join(sections)


async def write_cards(index: BidIndex, collector: Any, model: Model, *, bids: list[Bid] | None = None,
                      limit: int = 40, text_chars: int = 12000, max_attachments: int = 3) -> CardReport:
    """Cards for up to `limit` vague-title bids that have none: the given `bids` if any, else every such bid in the
    index. A bid that fails is left for the next run.

    Given `bids` only the documents already in the document cache are read, so no GeM request is
    made: a bid triage dropped has only its bid document there, and a bid with nothing cached is left for later."""
    agent = Agent(model, output_type=NativeOutput(CardOutput), instructions=CARD_INSTRUCTIONS, retries=2)
    report = CardReport()

    async def ask(bid: Bid, text: str) -> CardOutput:
        prompt = (f"Title: {bid.title}\nBuyer: {bid.ministry or ''} / {bid.department or ''}\n\n{text}")
        return (await agent.run(prompt, model_settings={"max_tokens": 400, "temperature": 0.0})).output

    if bids is None:
        todo = index.needing_cards(limit)
    else:
        todo = [b for b in bids if is_vague_title(b.title) and index.card(b.b_id) is None][:limit]
    for bid in todo:
        try:
            if bids is None:
                files = await collector.collect(bid)
            else:
                files = collector.cache.load(bid.b_id)
                if files is None or not any(f.role == "bid_document" and f.status == "ok" for f in files.files):
                    continue
            # the bid document, and for a reverse auction its original bid, which carries the subject
            docs = [f for f in files.files if f.role in ("bid_document", "parent_document") and f.status == "ok"]
            bid_text = "\n".join(p.text for d in docs for p in collector.cache.load_text(d))[:text_chars]
            sections = _rfp_sections(collector, files, bid_text, max_attachments)
            out = await ask(bid, f"=== Bid document ===\n{bid_text or '(no readable text)'}"
                                 + (f"\n\n{sections}" if sections else ""))
            if is_generic(out.subject, bid.title):
                index.set_card(bid.b_id, "")
                report.empty += 1
                continue
            terms = [t.strip() for t in out.terms if not is_generic(t, bid.title)]
            index.set_card(bid.b_id, out.subject.strip() + ("; " + "; ".join(terms) if terms else ""))
            report.written += 1
        except Exception as exc:  # noqa: BLE001 - one bid never stops the job
            log.warning("card for %s failed: %s", bid.bid_number, str(exc)[:200])
            report.failed += 1
    return report
