"""What the web chat reads (web/src/api/types.ts): RunView, BidResult, Understood, RunListItem."""
from __future__ import annotations

import re
from typing import Any, Literal

from gem_agent.agents.chat import short_name
from gem_agent.agents.evidence import too_short
from gem_agent.agents.judge import plausible_queries, proof
from gem_agent.cache.docs import DocCache
from gem_agent.cache.names import display_files, download_name, ensure_named
from gem_agent.graph.store import Outcome, RunRecord

GEM_DOC = "https://bidplus.gem.gov.in/showbidDocument/{}"


def _plain(meaning: str) -> str:
    """'AI Platform: Software platforms for …' → 'software platforms for …' (acronyms keep their capitals)."""
    text = (meaning.partition(": ")[2] or meaning).strip().rstrip(".")
    return text[:1].lower() + text[1:] if text[1:2].islower() else text


def understood_view(record: RunRecord) -> dict[str, Any] | None:
    """What the run looks for, stated once: a sentence, the window and the counts behind it."""
    req = record.request
    if req is None or not req.topics:
        return None
    topic = req.topics[0]
    name = " · ".join(short_name(t) for t in req.topics)
    window = req.window.describe()
    summary = (f"Looking for {_plain(topic.meaning)}, {window}." if len(req.topics) == 1
               else f"Looking for two meanings, {window}: {name}; results grouped, likeliest first.")
    return {"name": name, "meaning": topic.meaning.partition(": ")[2] or topic.meaning, "summary": summary,
            "buyers": list(req.buyer_hints), "window": window,
            "likely": sum(p.strong for p in record.probes),
            "not_this": topic.not_this}  # what the Judge rules out (the Planner and Searcher use it too)


def error_text(error: str) -> str:
    """Why a bid was not read, in words: the stored error can be GeM's whole HTML error page."""
    m = re.search(r":\s*(\d{3})\b", error)
    if m:
        code = int(m.group(1))
        why = "refused the request" if code in (401, 403, 429) else "had an error" if code >= 500 else "could not find it"
        return f"Not read: GeM {why} ({code}) when its documents were fetched. Searching again later may work."
    if "timeout" in error.lower() or "timed out" in error.lower():
        return "Not read: GeM did not answer in time. Searching again later may work."
    first = " ".join(error.split())
    return f"Not read: {first[:160]}{'…' if len(first) > 160 else ''}"


State = Literal["relevant", "borderline", "quarantined"]


def bid_state(o: Outcome) -> State:
    """What a bid is to the reader, decided in one place; the first rule that applies wins."""
    if o.restored:
        return "relevant"
    if o.ruling:
        return "relevant" if o.ruling.decision == "counts" else "quarantined"
    if o.verdict and o.verdict.borderline:
        return "borderline"
    return "relevant" if o.verdict and o.verdict.relevant else "quarantined"


def bid_result(run_id: str, o: Outcome) -> dict[str, Any]:
    v, b = o.verdict, o.bid
    groups = display_files(o.files)
    names = {r.filename: g.name for g in groups for r in g.records}  # quotes name the file they came from
    state = bid_state(o)
    ruling = o.ruling
    return {
        "b_id": b.b_id, "bid_number": b.bid_number, "title": b.title, "group": o.group,
        "buyer": " / ".join(p for p in (b.ministry, b.department) if p), "buyer_org": o.buyer_org,
        "published": b.published.isoformat() if b.published else "", "closing": b.closing.isoformat() if b.closing else "",
        "relevant": state == "relevant", "state": state,
        "question": v.question if v else "", "ruling": {"decision": ruling.decision, "by": ruling.by, "fact": ruling.fact}
        if ruling else None, "restored": o.restored, "confidence": v.confidence if v else 0.0,
        "reason": f"Your ruling: {ruling.fact}" if ruling and ruling.decision == "not"
        else v.reason if v else (error_text(o.error) if o.error else ""),
        # an unverified quote is either too short to count (it may well be there) or not found in the documents
        "evidence": [{**e.model_dump(), "file_name": names.get(e.file, e.file),
                      "check": "verified" if e.verified else "short" if too_short(e.quote) else "not_found"}
                     for e in v.evidence] if v else [],
        "key_facts": v.key_facts.model_dump() if v and v.key_facts else None,
        # the last date for queries: GeM's own pre-bid date, else the one read in the buyer's documents
        "queries": {**q.model_dump(mode="json"), "file_name": names.get(q.file, q.file)}
                   if v and (q := plausible_queries(v.queries, b.closing, b.published)) else None,
        "files": [{"name": g.name, "title": g.title, "also": g.also, "copies": len(g.records),
                   "filename": g.record.filename, "pages": g.record.pages, "status": g.record.status,
                   "download_name": download_name(b.bid_number, g),
                   "url": f"/api/runs/{run_id}/files/{b.b_id}/{g.record.sha256}"} for g in groups],
        "stage": v.stage if v else None,  # triage = ruled out from the title and first pages, without quotes
        "proof": proof(v.evidence, {r.filename: r.role for r in o.files}) if v and v.stage != "triage" else None,
        "needs_review": bool(v and v.needs_review), "gem_url": GEM_DOC.format(b.b_id), "error": o.error,
    }


def with_names(outcomes: dict[int, Outcome], cache: DocCache | None) -> dict[int, Outcome]:
    """Outcomes whose file lists carry names: runs stored before naming take them from the document cache."""
    return {b_id: o.model_copy(update={"files": ensure_named(b_id, o.files, cache)}) if o.files else o
            for b_id, o in outcomes.items()}


_MEANING_LINE = ("Looking for ", "Searching two meanings")
_COUNT_LINE = re.compile(r"^Read \d+ of \d+ bids: ")


def _progress(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Progress lines as the chat shows them.
    - No meaning line: older runs sent one right after `understood`; the card states it now.
    - Every running count carries kind "count"; the chat shows only the newest, updated in place."""
    lines = []
    for i, e in enumerate(events):
        if e.get("type") != "progress":
            continue
        line = e["event"]
        if i and events[i - 1].get("type") == "understood" and line["text"].startswith(_MEANING_LINE):
            continue
        if "kind" not in line and _COUNT_LINE.match(line["text"]):
            line = {**line, "kind": "count"}
        lines.append(line)
    return lines


def run_view(record: RunRecord, outcomes: dict[int, Outcome], events: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "run_id": record.run_id, "title": record.title, "request": record.request_text, "status": record.status,
        "created": record.created.isoformat(), "parent_run_id": record.parent_run_id,
        "understood": understood_view(record),
        "progress": _progress(events),
        "results": [bid_result(record.run_id, o) for o in outcomes.values() if o.verdict or o.error],
        "zip_url": f"/api/runs/{record.run_id}/zip",
    }


def run_list_item(record: RunRecord, outcomes: dict[int, Outcome]) -> dict[str, Any]:
    found = sum(1 for o in outcomes.values() if bid_state(o) == "relevant")
    return {"run_id": record.run_id, "title": record.title, "when": record.created.isoformat(), "found": found,
            "new_count": 0, "saved_search": False}
