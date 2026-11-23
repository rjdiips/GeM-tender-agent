"""Names people can read for a bid's files.

Three sources, no model and no extra requests:
1. The slot label the bid document gives each upload ("Scope of Work:1793964442.pdf").
2. The document's own heading ("RFP- 02/2026-27 for Supply, … of EFRMS"), or GeM's fixed pages by their URL.
3. Its text: copies with the same text are shown once, with every label and file name they were uploaded under.
A slot label wins unless the copies disagree (one RFP filed as "Payment Terms" and "Instructions to Bidder") or it
says nothing ("Additional Document 1"); then the heading names it, and the file name is the last resort.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from gem_agent.cache.docs import BidFiles, DocCache, FileRecord

_GEM_PAGES = [("/bidsla/", "Service level agreement (GeM)"), ("/showCatalogue/", "Catalogue (GeM)")]
_FIXED_ROLES = {"bid_document": "Bid document", "parent_document": "Original bid"}
_WEAK_LABEL = re.compile(r"^(?:additional|(?:any )?other|supporting|misc\w*)\b|^(?:document|file|attachment)\s*\d*$", re.IGNORECASE)
# a line that names a kind of document; a heading is looked for from the first such line
_DOC_WORDS = re.compile(
    r"request for (?:proposal|empanelment|quotation|information)|\b(?:rfp|rfe|rfq|eoi|nit)\b|tender|specification|"
    r"scope of|annexure|integrity pact|terms|conditions|price bid|commercial bid|financial bid|\bboq\b|"
    r"bill of quantit|eligibility|qualification|corrigendum|addendum|undertaking|agreement|notice|instructions|"
    r"declaration|format|proposal|bid document|expression of interest|note for|memorandum", re.IGNORECASE)
# a buyer's approval note or office memorandum is named by its subject line ("Sub: In principle approval for purchase of
# 60 Godrej … Chairs", 8237721), ahead of a letterhead line that happens to say "memo"
_SUBJECT = re.compile(r"^(?:sub|subject)\s*[:.]\s*(.+)$", re.IGNORECASE)  # not "Sub-head 3:", "Sub-criteria"
# a bracketed instruction under a heading ("(To be stamped as per the Stamp Law …)", "(Should be submitted on …)") is
# not part of it; a bracketed continuation is ("(ITMS) for JKRTC.")
_INSTRUCTION = re.compile(r"^\(\s*(?:to be|should|shall|must|please|on|in|to|kindly|submitted|signed|this|it)\b",
                          re.IGNORECASE)
_SALUTATION = re.compile(r"^(?:respected|dear)\b|^(?:sir|madam)\b", re.IGNORECASE)


def _new_heading(title: str, nxt: str) -> bool:
    """An all-capitals line after a mixed-case title is another heading ("… S4 HANA) BUYER'S ADDITIONAL TERMS")."""
    letters = [ch for ch in nxt if ch.isalpha()]
    return len(letters) >= 8 and nxt.upper() == nxt and title.upper() != title and not _MARKER.match(title)
# "Annexure-10" alone on a line heads the line after it
_MARKER = re.compile(r"^(?:annexure|annex|appendix|schedule|exhibit|enclosure)[\s\-–:]*[\w\-/().]{0,8}$", re.IGNORECASE)
_PAGE_LINE = re.compile(r"^(?:page\s*\d+(?:\s*of\s*\d+)?|\d+\s*\|\s*p\s*a\s*g\s*e|\d+)$", re.IGNORECASE)
_PORTAL = {"login", "sign up", "buyers", "sellers", "products"}
_ITEM_START = re.compile(r"^(?:\d+[.)]|[ivx]+[.)]|[a-z][.)]|page\b|rfp no|rfe no|tender no|bid no|dated?\b)", re.IGNORECASE)


def _shorten(text: str, max_chars: int) -> str:
    return text if len(text) <= max_chars else text[:max_chars].rsplit(" ", 1)[0].rstrip(" ,;:-") + "…"


def _ok_line(line: str) -> bool:
    letters = sum(ch.isascii() and ch.isalpha() for ch in line)
    return (letters >= 8 and letters / len(line) >= 0.6 and len(line.split()) >= 2
            and not _PAGE_LINE.match(line) and line.lower() not in _PORTAL)


_MARKS = set("®©¢@§¥£™°")
_SHORT_OK = {"a", "an", "as", "at", "be", "by", "do", "eg", "go", "id", "ie", "if", "in", "is", "it", "me", "my", "no", "of",
             "on", "or", "so", "to", "up", "us", "vs", "we", "&"}


def _junk(token: str) -> bool:
    """A token OCR made of a script it cannot read (Hindi read as Latin: "wa Ulead a AIA a ae oA ae"): a stray mark
    ("®", "¢1", "@t"), or one or two lowercase letters that are not a short English word. Capitals (IT, AI) and
    OCR's "Al" for AI pass, and so does punctuation (– | / ₹)."""
    word = token.strip("()[],.;:'\"")
    if any(ch in _MARKS for ch in word):
        return True
    return 0 < len(word) <= 2 and word.isalpha() and word.lower() not in _SHORT_OK and not word[0].isupper()


def _real_part(line: str) -> tuple[str, bool]:
    """The line up to where OCR noise takes over (from a junk token on, at least two and 3 in 10 tokens junk), and whether
    it was cut. Stray marks are dropped."""
    tokens = line.split()
    for i, token in enumerate(tokens):
        rest = tokens[i:]
        junk = sum(map(_junk, rest))
        if _junk(token) and junk >= 2 and junk / len(rest) >= 0.3:
            tokens = tokens[:i]
            return " ".join(t for t in tokens if not any(ch in _MARKS for ch in t)), True
    return " ".join(t for t in tokens if not any(ch in _MARKS for ch in t)), False


def doc_title(pages: list[Any], max_chars: int = 160) -> str | None:
    """The document's heading from its first page: the first line naming a kind of document, joined with the lines
    that continue it. None without such a line: OCR noise ("were atfereantise restr ese") passes any letter count,
    and a wrong name is worse than the file name."""
    lines = [" ".join(raw.split()) for p in pages[:1] for raw in p.text.splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    nonblank = [i for i, line in enumerate(lines) if line]
    lines = lines[: nonblank[24] + 1] if len(nonblank) > 25 else lines  # the first 25 lines with text, blanks kept:
    # a heading stops at a blank line
    good = [i for i, line in enumerate(lines) if _ok_line(line)]
    subject = next((i for i, line in enumerate(lines) if (m := _SUBJECT.match(line)) and _ok_line(m.group(1))), None)
    marker = next((i for i, line in enumerate(lines[:-1]) if _MARKER.match(line) and _ok_line(lines[i + 1])), None)
    named = next((i for i in good if _DOC_WORDS.search(lines[i])), None)
    found = [i for i in (subject, marker, named) if i is not None]
    if not found:
        return None
    start = min(found)  # the first heading on the page wins
    if start == subject:
        lines = [*lines[:start], _SUBJECT.match(lines[start]).group(1), *lines[start + 1:]]  # type: ignore[union-attr]
    title, cut = _real_part(lines[start])
    for nxt in lines[start + 1 :]:
        if (cut or not _ok_line(nxt) or _ITEM_START.match(nxt) or _INSTRUCTION.match(nxt) or _SUBJECT.match(nxt)
                or _SALUTATION.match(nxt) or _new_heading(title, nxt) or title.endswith((".", ":", ","))
                or len(title) >= max_chars):
            break
        part, cut = _real_part(nxt)
        title = f"{title} {part}".strip()
    title = re.sub(r"(?:\s+(?:&|and|of|for|the|to|in|on|or|by))+$", "", title.strip(" .:;,\"'“”&"), flags=re.IGNORECASE)
    return _shorten(title, max_chars) or None


# GeM's own Custom Bid slots, whose labels are instructions rather than names
_GEM_SLOTS = [("undertaking of competent authority", "Undertaking of Competent Authority"),
              ("introduction about the project", "Project introduction"),
              ("educational qualification including profile", "Team qualifications and profiles"),
              ("resources if they are part of project", "Team qualifications and profiles"),  # its second line
              ("quantifiable specification", "Specifications and service standards (BOQ)"),
              ("buyer to upload undertaking that minimum wages", "Minimum wages undertaking"),
              ("buyer to upload gazette notification", "Minimum wages notification"),
              ("format to be uploaded by buyer for the breakup", "Cost breakup format"),
              ("buyer to upload machinery or consumable list", "Machinery and consumables list")]
# a line that ends a slot (a file name), heads the section, or marks a page: a wrapped label does not run past it
_LABEL_STOP = re.compile(r"\S+\.\w{2,5}$|required\s*:?$|^\d+\s*/\s*\d+$|[^\x00-\x7f]", re.IGNORECASE)


def _clean_label(text: str) -> str:
    text = " ".join(text.split())
    for prefix, name in _GEM_SLOTS:
        if text.lower().startswith(prefix):
            return name
    text = re.sub(r"\(\s*(.*?)\s*\)", r"(\1)", text)  # "( PQC )" → "(PQC)"
    text = re.split(r"\s+etc\b", text, maxsplit=1, flags=re.IGNORECASE)[0]
    if len(text) > 60:
        text = re.split(r"(?<=\w)\.\s", text, maxsplit=1)[0]
    return _shorten(text.strip(" .:-"), 60)


def _labels(bid_text: str, filenames: list[str]) -> dict[str, str]:
    """The slot label the bid document gives each file: "Scope of Work:1793964442.pdf", a label wrapped onto a line
    that starts in lowercase (a capital may start the next table cell: "As per RFP⏎Scope of Work:…") ("Introduction about the project … custom bid⏎functionality:1793862909.pdf"), or the Excel slot
    ("Excel Upload Required :⏎Financial bid - 1793615006.xlsx")."""
    out: dict[str, str] = {}
    lines_before = 2
    for name in filenames:
        for m in re.finditer(re.escape(name), bid_text):
            head = bid_text[max(0, m.start() - 400) : m.start()].split("\n")
            line = head[-1].rstrip()
            if line.endswith(" -") and len(head) > 1 and re.search(r"upload required\s*:?$", head[-2], re.IGNORECASE):
                label = line[:-2]
            elif line.endswith(":"):
                parts = [line[:-1]]
                for prev in reversed(head[-1 - lines_before : -1]):
                    prev = prev.strip()
                    if not prev or _LABEL_STOP.search(prev) or not parts[0][:1].islower():
                        break
                    parts.insert(0, prev)
                label = re.split(r"\S+\.\w{2,4}\s|:", " ".join(parts))[-1]
            else:
                continue
            label = _clean_label(label)
            if len(label) >= 2 and re.search(r"[A-Za-z]{2}", label):
                out[name] = label
                break
    return out


def _text_key(pages: list[Any]) -> str:
    text = " ".join(" ".join(p.text.split()) for p in pages)
    return hashlib.sha1(text.encode()).hexdigest()[:16] if text.strip() else ""


def name_files(files: BidFiles, cache: DocCache) -> bool:
    """Fill each readable file's label, heading and text key; True if anything changed (then save the manifest).
    A file is named once: its text key is set, "" when it has no text."""
    todo = [r for r in files.files if r.role != "gtc" and r.text_key is None]
    if not todo:
        return False
    docs = [r for r in files.files if r.role in ("bid_document", "parent_document") and r.status == "ok"]
    bid_text = "\n".join(p.text for d in docs for p in cache.load_text(d))
    labels = _labels(bid_text, [r.filename for r in todo if r.role not in _FIXED_ROLES])
    for record in todo:
        pages = cache.load_text(record) if record.status == "ok" else []
        record.label = labels.get(record.filename)
        record.title = next((name for part, name in _GEM_PAGES if part in record.url), None) or doc_title(pages)
        record.text_key = _text_key(pages)
    return True


def ensure_named(b_id: int, records: list[FileRecord], cache: DocCache | None) -> list[FileRecord]:
    """`records` with names, taken from the cached manifest (named and saved now if it was not yet): a run's stored
    outcome keeps the file list from when the run happened, and the cache has the latest naming."""
    if cache is None:
        return records
    manifest = cache.load(b_id)
    if manifest is None:
        return records
    if name_files(manifest, cache):
        cache.save(manifest)
    named = {(r.filename, r.sha256): r for r in manifest.files}
    out = []
    for r in records:
        m = named.get((r.filename, r.sha256))
        out.append(r.model_copy(update={"label": m.label, "title": m.title, "text_key": m.text_key,
                                                "pages": m.pages}) if m else r)
    return out


@dataclass
class FileGroup:
    """One document as shown: identical copies together, the first one stands for them."""
    name: str
    title: str | None
    records: list[FileRecord]
    also: list[str] = field(default_factory=list)  # the other labels and file names it was uploaded under

    @property
    def record(self) -> FileRecord:
        return self.records[0]


def _name(records: list[FileRecord]) -> str:
    first = records[0]
    if first.role in _FIXED_ROLES:
        return _FIXED_ROLES[first.role]
    labels = list(dict.fromkeys(r.label for r in records if r.label))
    strong = [lbl for lbl in labels if not _WEAK_LABEL.search(lbl)]
    if len(strong) == 1:
        return strong[0]
    # copies filed under different slots: one RFP in every slot is named by its heading, but a heading taken from
    # body text ("5 years prior to the original scheduled date of tender") loses to a slot ("Pre Qualification …")
    if first.title and (not strong or _DOC_WORDS.search(first.title[:40])):
        return first.title
    return strong[0] if strong else (labels[0] if labels else first.filename)


def display_files(records: list[FileRecord]) -> list[FileGroup]:
    """The files to show, in their original order: GeM's general terms left out, identical copies once."""
    groups: dict[str, list[FileRecord]] = {}
    for r in records:
        if r.role == "gtc" or (r.status == "empty" and not r.label):  # GeM's empty links ("downloadOmppdfile", 0 bytes)
            continue
        key = f"text:{r.text_key}" if r.status == "ok" and r.text_key else f"file:{r.sha256 or r.filename}:{r.url}"
        groups.setdefault(key, []).append(r)
    out = []
    for copies in groups.values():
        name = _name(copies)
        if len(copies) == 1:
            also = [copies[0].label] if copies[0].label and copies[0].label != name else []
        else:
            also = [n for n in dict.fromkeys(r.label or r.filename for r in copies) if n != name]
        out.append(FileGroup(name=name, title=copies[0].title, records=copies, also=also))
    # different texts under one heading are versions, not copies: 10115089's RFP came in two, scoring technical
    # evaluation out of 300 and out of 100. Numbered in upload order (GeM's upload ids rise).
    by_name: dict[str, list[FileGroup]] = {}
    for group in out:
        by_name.setdefault(group.name, []).append(group)
    for same in by_name.values():
        if len(same) > 1 and same[0].record.role not in _FIXED_ROLES:
            for n, group in enumerate(sorted(same, key=lambda g: g.record.filename), 1):
                group.name = f"{_shorten(group.name, 80)} (version {n})"  # the version must survive any cut
    return out


_KIND_EXT = {"pdf": ".pdf", "html": ".html"}


def download_name(bid_number: str, group: FileGroup) -> str:
    """"GEM-2026-B-8229813 - Scope of Work.pdf": the bid, what the file is, and an extension that opens it."""
    record = group.record
    suffix = re.search(r"\.\w{2,5}$", record.filename)
    ext = suffix.group(0) if suffix else _KIND_EXT.get(record.kind, "")
    base, version = re.match(r"(.*?)( \(version \d+\))?$", group.name).groups()
    name = _shorten(base, 90).rstrip("…").strip() + (version or "")
    if ext and name.lower().endswith(ext.lower()):  # still named by its file name: "RFP.pdf", not "RFP.pdf.pdf"
        name = name[: -len(ext)]
    safe = re.sub(r"[^\w.()&, -]", "_", f"{bid_number.replace('/', '-')} - {name}")
    return safe.rstrip(" ._") + ext
