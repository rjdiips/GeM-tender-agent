"""Text helpers for bid descriptions: the model output schema, generic-subject check and RFP excerpts.

GeM's first pages often repeat only the category; the subject is in buyer-added terms further into the bid document,
or in an attachment labelled "Scope of Work". A reverse auction's subject can be in its original bid, and only an RFP's
scope section may name the modules inside a broad subject. The extraction helpers therefore read each RFP's first
pages, scope-of-work sections and title-like lines wherever they occur.
"""
from __future__ import annotations

import re
from typing import Annotated, Any

from pydantic import BaseModel, Field, StringConstraints

_GENERIC = {"hiring", "of", "agency", "for", "it", "projects", "project", "milestone", "basis", "custom", "bid",
            "services", "service", "software", "development", "the", "and", "a", "an", "solution", "solutions",
            "work", "works", "as", "per", "rfp", "implementation", "procurement", "supply"}

class CardOutput(BaseModel):
    subject: str = Field(max_length=200)
    terms: list[Annotated[str, StringConstraints(max_length=60)]] = Field(max_length=8)


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def is_generic(subject: str, title: str) -> bool:
    """Empty, or only words of the title and generic procurement words."""
    words = _words(subject)
    return not words or words <= (_words(title) | _GENERIC)


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


def rfp_sections(collector: Any, files: Any, bid_text: str, max_files: int = 3) -> tuple[str, list[str]]:
    """Up to `max_files` attachments, the one labelled "Scope of Work" first, then the largest (the RFP); identical
    content uploaded under several labels counts once; placeholders and GeM's general terms are skipped. Returns the
    prompt text and the attachment file names represented in it."""
    labels = {name: label.strip().lower() for label, name in _LABEL.findall(bid_text)}
    labels |= {f.filename: f.label.lower() for f in files.files if f.label}  # parsed by the collector (cache/names.py)
    attachments = [f for f in files.files if f.role in ("attachment", "archive_member") and f.status == "ok"]
    attachments.sort(key=lambda f: (labels.get(f.filename) != "scope of work", -f.pages))
    # identical content uploaded under several names counts once, but every name is shown
    groups: dict[int, list[Any]] = {}
    for record in attachments:
        pages = collector.cache.load_text(record)
        groups.setdefault(hash(" ".join(p.text for p in pages)[:5000]), []).append((record, pages))
    sections, sources = [], []
    for copies in list(groups.values())[:max_files]:
        record, pages = copies[0]
        also = f" (also uploaded as: {', '.join(r.filename for r, _ in copies[1:])})" if len(copies) > 1 else ""
        sections.append(f"=== Attachment {record.filename} ({record.pages} pages){also} ===\n{_attachment_text(pages)}")
        sources.extend(r.filename for r, _ in copies)
    return "\n\n".join(sections), sources


def _rfp_sections(collector: Any, files: Any, bid_text: str, max_files: int = 3) -> str:
    """Text-only compatibility wrapper for eval scripts."""
    return rfp_sections(collector, files, bid_text, max_files)[0]
