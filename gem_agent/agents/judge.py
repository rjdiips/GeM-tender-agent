"""Judge: triage, per-bid retrieval, verdict with evidence.

Per bid:
1. Triage on title, buyer and bid PDF pages 1-2 (~1.5k tokens). A clear no stops here.
2. Passages for the topic from every readable file (not GeM's general terms), plus the scope-of-work and
   eligibility passages, within a ~10k-token budget.
3. A verdict with 1-3 quotes. Every quote is checked against the cited page; a relevant verdict whose quotes
   can't be found goes back once, then is marked for review.
4. A borderline verdict (low confidence) reads the main documents in steps of ~20 pages, then decides again.
The fixed instructions and the topic come first in every prompt, so consecutive calls share a prefix.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.models import Model

from gem_agent.agents.evidence import verify_quote
from gem_agent.agents.schemas import (
    Evidence,
    JudgeOutput,
    NotesOutput,
    QueriesDeadline,
    QueriesOut,
    TopicDefinition,
    TriageOutput,
    Verdict,
)
from gem_agent.cache.docs import BidFiles, DocCache, FileRecord
from gem_agent.cache.verdicts import VerdictCache
from gem_agent.retrieval.chunks import Chunk, file_labels, make_chunks
from gem_agent.retrieval.embed import Embedder
from gem_agent.retrieval.passages import find_passages
from gem_agent.retrieval.rerank import Reranker
from gem_agent.tools.gem.models import Bid, site_now
from gem_agent.tools.gem.prebid import parse_pre_bid

JUDGE_PROMPT_VERSION = "1"  # bump whenever the triage or judge instructions change: it keys the verdict cache

TRIAGE_INSTRUCTIONS = """You screen Indian government (GeM) tenders for one topic.
Read the topic definition, then the tender's title, buyer and first pages.
Answer clearly_no only when the tender is plainly about something else.
Answer clearly_yes only when the text plainly shows the topic itself. GeM's "Similar Category" list is filled in
from the item category, not written by the buyer, so it never shows the topic on its own.
Otherwise answer maybe.
Look-alike words listed under "NOT this" do not count as the topic. Things listed as "related but not enough" on
their own mean maybe at most.
Reason: one short sentence."""

JUDGE_INSTRUCTIONS = """You decide whether a GeM tender is really about one topic, by reading passages from its documents.
- Decide on meaning, not on matching words. A look-alike listed under "NOT this" is not the topic. Something listed
  as "related but not enough" makes the tender relevant only if the topic itself is also in its scope.
- Evidence: copy 1-3 quotes word for word from the passages (8-40 words each), each with the passage label
  (e.g. F2) and the page number shown in the passage header. Never paraphrase inside a quote. Quote what the
  documents say is bought (scope, specifications, terms), whichever way you decide; the "Tender title", "Bid number"
  and "Buyer" lines at the top are a summary, not the documents, so never quote them.
- In scope means bought now. The topic named only as a possible future extension ("future integration of machine
  learning", "may later include"), or only in GeM's "Similar Category" list on the bid document's first page (GeM
  fills it in from the item category; the buyer did not write it), is not in scope.
- If the passages don't settle it, answer with a low confidence.
- Borderline: the documents show something close to the topic, and whether it counts depends on the buyer's purpose or the reader's line of business, not on missing evidence. Then ask, in one sentence, the specific question the reader must answer (e.g. "AI is one part of a digital twin platform. Does that count as an AI platform for you?"). Otherwise leave question empty.
- Key facts: fill in EMD, minimum turnover, contract period, estimated value and a short scope summary when the
  passages state them; leave them empty otherwise.
- Queries: the last date and time for bidders to send queries or pre-bid clarifications, when the passages state
  it (often on an RFP's cover page or in a schedule of dates; only when none is given, the pre-bid meeting): what
  kind of date your quote states, the exact words including the date itself, then the date as numbers (5.00 PM is
  hour 17; hour null when no time is given). Kind "none" and all null when no such date is written out. Never give
  the bid closing or opening date as the queries date.
- Reason: one sentence a sales person can read, saying what the tender actually buys."""

QUERIES_INSTRUCTIONS = """You read passages from an Indian government (GeM) tender's documents.
Find the last date and time for bidders to send queries or pre-bid clarifications (often on an RFP's cover page or
in a schedule of dates); only when none is given, the pre-bid meeting date. Say what kind of date your quote states,
copy the exact words including the date itself, then give the date as numbers (5.00 PM is hour 17; hour null when
no time is given). A sentence about queries that names no date is not an answer. If no such date is written out, answer kind "none"
and all null."""

NOTES_INSTRUCTIONS = """You read part of a GeM tender's documents for one topic.
Write short notes on what these pages say about the topic, citing page numbers.
Write nothing if the pages don't mention it. At most 120 words."""

TITLE_PAGES = 2  # the bid PDF's first pages repeat the listing (title, category, buyer); the buyer-added terms follow
QUERY_TERMS = ["queries", "pre-bid", "pre bid", "clarification", "clarifications"]
QUERY_SEARCH = "last date and time for submission of queries, pre-bid queries and clarifications, pre-bid meeting"
SCOPE_TERMS = ["scope of work", "eligibility", "pre-qualification", "technical specification", "requirements"]


class JudgeSettings(BaseModel):
    triage_chars: int = 5000  # bid PDF pages 1-2 for triage (~1.5k tokens)
    bid_pages_chars: int = 6000
    context_chars: int = 46000  # ~13k tokens for the verdict
    top_k: int = 8
    scope_k: int = 3
    queries_k: int = 2  # opening pages and matches for the queries date, shown first; skipped when GeM's own
    # pre-bid date is known
    deep_below: float = 0.6
    deep_pages_per_call: int = 20
    deep_max_pages: int = 120
    deep_batch_chars: int = 60000
    triage_max_tokens: int = 150
    judge_max_tokens: int = 850  # 700 before the queries date and its quote
    notes_max_tokens: int = 300
    queries_max_tokens: int = 200
    parallel: int = 2  # model calls at a time


Proof = Literal["documents", "title", "none"]


def proof(evidence: list[Evidence], roles: dict[str, str]) -> Proof:
    """What a verdict's quotes prove, shown in place of the model's own confidence.
    documents: a verified quote from beyond the title: an attachment, an RA's original bid, or the bid document past
      its first two pages (the buyer-added terms, often the only place a bid without attachments says what it buys).
    title: verified quotes only from the bid document's first pages, which repeat the listing. GeM's general terms
      are the same for every bid, so a quote from them counts as title at most.
    none: no quote was found in the documents."""
    verified = [e for e in evidence if e.verified]
    if any(roles.get(e.file) != "gtc" and not (roles.get(e.file) == "bid_document" and e.page <= TITLE_PAGES)
           for e in verified):
        return "documents"
    return "title" if verified else "none"


def verdict_version(model: str) -> str:
    return hashlib.sha256(f"{model}|{JUDGE_PROMPT_VERSION}".encode()).hexdigest()[:8]


def doc_hash(files: BidFiles) -> str:
    data = [(f.sha256, f.status) for f in files.files]
    corr = files.corrigendum.model_dump(mode="json") if files.corrigendum else None
    return hashlib.sha256(json.dumps([data, corr]).encode()).hexdigest()[:16]


def _file_label(cited: str, labels: list[tuple[str, FileRecord]]) -> str:
    """The file label a quote cites, however the model wrote it: "F3", its file name, or the whole passage header
    "[F3 p.1 | 1794202410.pdf]"."""
    known = {label for label, _ in labels}
    if cited in known:
        return cited
    by_name = {record.filename: label for label, record in labels}
    if cited.strip() in by_name:
        return by_name[cited.strip()]
    m = re.search(r"\bF\d+\b", cited)
    if m and m.group(0) in known:
        return m.group(0)
    return next((label for name, label in by_name.items() if name in cited), cited)


QUERY_KINDS = ("queries_deadline", "pre_bid_meeting")


def _states_date(quote: str, when: datetime) -> bool:
    """The quote has the date's day and year in it: a sentence about queries with no date ("on or before the date
    given", 8263340) does not support the date the model gave with it."""
    numbers = {int(n) for n in re.findall(r"\d+", quote)}
    return when.day in numbers and (when.year in numbers or when.year % 100 in numbers)


def queries_date(out: QueriesOut) -> datetime | None:
    """The Judge's queries date; a day without a time counts until its end. None if incomplete or impossible."""
    if out.year is None or out.month is None or out.day is None:
        return None
    try:
        if out.hour is None:
            return datetime(out.year, out.month, out.day, 23, 59)
        return datetime(out.year, out.month, out.day, out.hour, out.minute or 0)
    except ValueError:
        return None


def plausible_queries(queries: QueriesDeadline | None, closing: datetime | None,
                      published: datetime | None = None) -> QueriesDeadline | None:
    """Queries close after the bid is published and before it closes. A date on or after closing is the closing date
    misread; one before publication is a typo or misreading."""
    if queries is None:
        return None
    if closing is not None and queries.when >= closing:
        return None
    if published is not None and queries.when.date() < published.date():
        return None
    return queries


class Judge:
    def __init__(
        self,
        model: Model,
        cache: DocCache,
        verdicts: VerdictCache,
        *,
        embedder: Embedder | None = None,
        reranker: Reranker | None = None,
        settings: JudgeSettings | None = None,
        now: Callable[[], datetime] = site_now,
    ) -> None:
        self.cache, self.verdicts = cache, verdicts
        self.embedder, self.reranker = embedder, reranker
        self.settings = settings or JudgeSettings()
        self.now = now
        self.model_name = model.model_name
        self._triage = Agent(model, output_type=NativeOutput(TriageOutput), instructions=TRIAGE_INSTRUCTIONS)
        self._judge = Agent(model, output_type=NativeOutput(JudgeOutput), instructions=JUDGE_INSTRUCTIONS)
        self._queries_only = Agent(model, output_type=NativeOutput(QueriesOut), instructions=QUERIES_INSTRUCTIONS)
        self._notes = Agent(model, output_type=NativeOutput(NotesOutput), instructions=NOTES_INSTRUCTIONS)
        self._slots = asyncio.Semaphore(self.settings.parallel)

    async def _ask(self, agent: Agent[None, Any], prompt: str, max_tokens: int) -> Any:
        async with self._slots:
            result = await agent.run(prompt, model_settings={"max_tokens": max_tokens, "temperature": 0.0})
        return result.output

    def _base(self, topic: TopicDefinition, bid: Bid, files: BidFiles) -> dict[str, Any]:
        return {"b_id": bid.b_id, "bid_number": bid.bid_number, "topic_key": topic.key(), "doc_hash": doc_hash(files),
                "model": self.model_name}

    def _pages(self, files: BidFiles) -> tuple[list[tuple[str, FileRecord]], dict[tuple[str, int], str], str]:
        labels = file_labels(files)
        pages = {(label, p.page): p.text for label, record in labels for p in self.cache.load_text(record)}
        return labels, pages, self._bid_pages(labels, pages)

    async def screen(self, topic: TopicDefinition, bid: Bid, files: BidFiles) -> tuple[Verdict | None, TriageOutput | None]:
        """Triage on the bid document alone. Returns a verdict when the bid is already decided (cached, or a clear
        no, which is stored), else None and the triage answer to pass to judge()."""
        base = self._base(topic, bid, files)
        cached = self.verdicts.get(bid.b_id, base["topic_key"], base["doc_hash"])
        if cached:
            return cached, None
        labels, pages, bid_text = self._pages(files)
        triage: TriageOutput = await self._ask(
            self._triage, self._triage_prompt(topic, bid, bid_text, files.buyer_org), self.settings.triage_max_tokens)
        if triage.decision == "clearly_no":
            verdict = Verdict(**base, relevant=False, confidence=0.9, stage="triage", triage=triage.decision,
                              reason=triage.reason, queries=self._gem_pre_bid(labels, pages), decided_at=self.now())
            self.verdicts.put(verdict)
            return verdict, triage
        return None, triage

    async def judge(self, topic: TopicDefinition, bid: Bid, files: BidFiles,
                    triage: TriageOutput | None = None) -> Verdict:
        base = self._base(topic, bid, files)
        cached = self.verdicts.get(bid.b_id, base["topic_key"], base["doc_hash"])
        if cached:
            return cached
        if triage is None:
            decided, triage = await self.screen(topic, bid, files)
            if decided:
                return decided
        assert triage is not None
        labels, pages, bid_text = self._pages(files)
        gem_pre_bid = self._gem_pre_bid(labels, pages)

        chunks = make_chunks(files, self.cache)
        context = self._context(topic, bid, chunks, bid_text, self.settings.context_chars, files.buyer_org,
                                queries=gem_pre_bid is None)
        out, evidence, review = await self._decide(topic, context, labels, pages)
        stage = "judge"
        if out.confidence < self.settings.deep_below:
            notes = await self._deep_read(topic, labels)
            if notes:
                room = self.settings.context_chars - len(notes)
                deep_context = notes + "\n\n" + self._context(topic, bid, chunks, bid_text, max(room, 8000),
                                                             files.buyer_org, queries=gem_pre_bid is None)
                out, evidence, review = await self._decide(topic, deep_context, labels, pages)
                stage = "deep"
        confidence = min(out.confidence, 0.5) if review else out.confidence  # unproven: never high confidence
        borderline = out.decision == "borderline"
        question = (out.question.strip() or out.reason) if borderline else ""
        verdict = Verdict(**base, relevant=out.decision == "relevant", borderline=borderline, question=question,
                          confidence=confidence, stage=stage,
                          triage=triage.decision, reason=out.reason, evidence=evidence, key_facts=out.key_facts,
                          queries=plausible_queries(gem_pre_bid or self._queries(out.queries, labels, pages), bid.closing,
                                                    bid.published),
                          needs_review=review, decided_at=self.now())
        self.verdicts.put(verdict)
        return verdict

    # --- the verdict, with its quotes checked --------------------------------------------------------------------

    async def _decide(
        self, topic: TopicDefinition, context: str, labels: list[tuple[str, FileRecord]],
        pages: dict[tuple[str, int], str],
    ) -> tuple[JudgeOutput, list[Evidence], bool]:
        prompt = f"{topic.render()}\n\n{context}"
        out: JudgeOutput = await self._ask(self._judge, prompt, self.settings.judge_max_tokens)
        evidence = self._check(out, labels, pages)
        if out.decision != "not_relevant" and not any(e.verified for e in evidence):
            missing = "; ".join(f'"{q.quote}" ({q.file} p.{q.page})' for q in out.evidence) or "none given"
            retry = (f"{prompt}\n\nYour previous answer quoted text that could not be found in the passages: "
                     f"{missing}. Copy each quote exactly from the passages above, with its label and page.")
            out = await self._ask(self._judge, retry, self.settings.judge_max_tokens)
            evidence = self._check(out, labels, pages)
        if any(e.verified for e in evidence) and not self._from_documents(evidence, labels):
            # the bid PDF's first pages repeat the tender's own title, so a quote from them proves little:
            # ask for a passage from the buyer-added terms, an attachment or the original bid. A rejection too:
            # 8182010 was ruled out "95% sure" on its title, while its scope (a land survey) said why in plain words
            retry = (f"{prompt}\n\nYour quotes all come from the first pages of the bid document, which repeat the "
                     f"tender's own title. Quote a passage that says what is bought: from an attachment (an RFP, a "
                     f"scope of work, the terms) or the bid document's buyer-added terms, with its label and page.")
            out = await self._ask(self._judge, retry, self.settings.judge_max_tokens)
            evidence = self._check(out, labels, pages)
        review = out.decision == "relevant" and not (any(e.verified for e in evidence) and self._from_documents(evidence, labels))
        return out, evidence, review

    # --- the last date for queries ------------------------------------------------------------------------------------

    @staticmethod
    def _gem_pre_bid(labels: list[tuple[str, FileRecord]],
                     pages: dict[tuple[str, int], str]) -> QueriesDeadline | None:
        """GeM's own pre-bid date, printed in a fixed block on its bid document: exact, and needs no model."""
        for label, record in labels:
            if record.role != "bid_document":
                continue
            own = sorted((page, text) for (lbl, page), text in pages.items() if lbl == label)
            # each page, then each page joined to the next: the block can straddle a page break
            for i, (page, text) in enumerate(own):
                found = parse_pre_bid(text)
                if not found and i + 1 < len(own):
                    found, page = parse_pre_bid(text + "\n" + own[i + 1][1]), own[i + 1][0]
                if found:
                    return QueriesDeadline(when=found[0], source="gem_pre_bid", kind="pre_bid_meeting",
                                           quote=found[1], file=record.filename, page=page, verified=True)
        return None

    @staticmethod
    def _queries(out: QueriesOut, labels: list[tuple[str, FileRecord]],
                 pages: dict[tuple[str, int], str]) -> QueriesDeadline | None:
        """The queries date the Judge read, kept with the page its words were found on. The model names no file for
        it, so every page is searched; words found nowhere keep the date, marked unverified."""
        when = queries_date(out)
        if when is None or out.kind not in QUERY_KINDS or not _states_date(out.quote or "", when):
            return None
        names = {label: record.filename for label, record in labels}
        quote = (out.quote or "").strip()
        hit = next(((lbl, p) for (lbl, p), text in sorted(pages.items()) if quote and verify_quote(quote, text)), None)
        label, page = hit or (labels[0][0] if labels else "", 0)
        return QueriesDeadline(when=when, source="documents", kind=out.kind, quote=quote,
                               file=names.get(label, label), page=page,
                               verified=hit is not None)

    @staticmethod
    def _from_documents(evidence: list[Evidence], labels: list[tuple[str, FileRecord]]) -> bool:
        return proof(evidence, {record.filename: record.role for _, record in labels}) == "documents"

    @staticmethod
    def _check(out: JudgeOutput, labels: list[tuple[str, FileRecord]],
               pages: dict[tuple[str, int], str]) -> list[Evidence]:
        names = {label: record.filename for label, record in labels}
        evidence = []
        for q in out.evidence:
            label, page = _file_label(q.file, labels), q.page
            q = q.model_copy(update={"file": label})
            label = q.file
            verified = verify_quote(q.quote, pages.get((label, page), ""))
            if not verified:
                # the right file, the wrong page: a docx is one page sent as windows all marked p.1, and the model
                # cited p.2 and p.3; a PDF quote can be a page off. Then the wrong file: 8266583
                # quoted the bid document's "Similar Category" list as the Scope of Work. The cited file is
                # searched first, then the others; the file and page are corrected.
                ordered = sorted(pages.items(), key=lambda kv: (kv[0][0] != q.file, kv[0]))
                label, page = next(((lbl, p) for (lbl, p), text in ordered
                                    if (lbl, p) != (q.file, q.page) and verify_quote(q.quote, text)), (q.file, q.page))
                verified = (label, page) != (q.file, q.page)
            evidence.append(Evidence(quote=q.quote, file=names.get(label, label), page=page, verified=verified))
        return evidence

    # --- prompts ------------------------------------------------------------------------------------------------

    def _bid_pages(self, labels: list[tuple[str, FileRecord]], pages: dict[tuple[str, int], str]) -> str:
        label = next((lbl for lbl, r in labels if r.role == "bid_document"), None)
        if label is None:
            return ""
        return "\n".join(pages.get((label, n), "") for n in (1, 2)).strip()

    @staticmethod
    def _header(bid: Bid, org: str | None = None) -> str:
        buyer = " / ".join(p for p in (bid.ministry, bid.department) if p) or "unknown"
        if org:
            buyer += f" / {org}"
        return f"Tender title: {bid.title}\nBid number: {bid.bid_number}\nBuyer: {buyer}"

    def _triage_prompt(self, topic: TopicDefinition, bid: Bid, bid_text: str, org: str | None = None) -> str:
        return (f"{topic.render()}\n\n{self._header(bid, org)}\n\n=== Bid document, pages 1-2 ===\n"
                f"{bid_text[: self.settings.triage_chars]}")

    def _context(self, topic: TopicDefinition, bid: Bid, chunks: list[Chunk], bid_text: str, budget: int,
                 org: str | None = None, *, queries: bool = False) -> str:
        s = self.settings
        head = f"{self._header(bid, org)}\n\n=== Bid document, pages 1-2 ===\n{bid_text[: s.bid_pages_chars]}\n"
        parts, used = [head[:budget]], min(len(head), budget)
        terms = [*topic.match_terms, *topic.acronyms]
        # the queries date sits on a cover page or a schedule that never uses the topic's words,
        # so it gets passages of its own, shown first so the budget can't drop them: the opening page of the largest
        # attachments (a cover page states a date once, and a "Queries" clause saying how to send them outranked it)
        # and the best matches for the date itself
        dated = self._queries_passages(chunks) if queries else []
        picked = find_passages(chunks, query=topic.meaning, terms=terms, top_k=s.top_k, embedder=self.embedder,
                               reranker=self.reranker)
        picked += find_passages(chunks, query="scope of work and eligibility", terms=SCOPE_TERMS, top_k=s.scope_k)
        if not picked:  # nothing matched any word: show the start of the main attachment
            main = max((c.label for c in chunks if c.role != "bid_document"),
                       key=lambda lbl: sum(1 for c in chunks if c.label == lbl), default=None)
            picked = [c for c in chunks if c.label == main][: s.top_k]
        # then the opening page of every attachment not shown yet: a tender about something else never uses the
        # topic's words, so matching alone showed 8182010's judge only GeM's standard pages, never its scope (a land
        # survey) or terms, and the rejection could only cite the title
        shown_files = {c.label for c in picked}
        openings = [next(c for c in chunks if c.label == label) for label in dict.fromkeys(c.label for c in chunks)
                    if label not in shown_files and any(c.role != "bid_document" for c in chunks if c.label == label)]
        seen = set()
        for chunk in [*dated, *picked, *openings]:
            if chunk.role == "bid_document" and chunk.page <= TITLE_PAGES:
                continue  # already in the head
            key = " ".join(chunk.text.split())  # the same RFP uploaded twice shows once
            if key in seen:
                continue
            seen.add(key)
            block = f"\n[{chunk.label} p.{chunk.page} | {chunk.filename}]\n{chunk.text}\n"
            room = budget - used
            if room < 500:
                break
            parts.append(block[:room])
            used += min(len(block), room)
        return "".join(parts)

    def _queries_passages(self, chunks: list[Chunk]) -> list[Chunk]:
        k = self.settings.queries_k
        sizes = {c.label: sum(1 for x in chunks if x.label == c.label) for c in chunks
                 if c.role not in ("bid_document", "gtc")}
        largest = sorted(sizes, key=lambda lbl: -sizes[lbl])[:k]
        dated = [next(c for c in chunks if c.label == lbl) for lbl in largest]
        return dated + find_passages(chunks, query=QUERY_SEARCH, terms=QUERY_TERMS, top_k=k, embedder=self.embedder)

    async def find_queries(self, bid: Bid, files: BidFiles, *, ask: bool = True) -> tuple[QueriesDeadline | None, bool]:
        """The queries date alone, for older results: GeM's pre-bid date, else (when `ask`) one small
        call over the queries passages. Returns the date and whether the model was asked."""
        labels, pages, _ = self._pages(files)
        gem = self._gem_pre_bid(labels, pages)
        if gem or not ask:
            return plausible_queries(gem, bid.closing, bid.published), False
        parts, seen = [self._header(bid, files.buyer_org)], set()
        for chunk in self._queries_passages(make_chunks(files, self.cache)):
            key = " ".join(chunk.text.split())
            if key not in seen:
                seen.add(key)
                parts.append(f"[{chunk.label} p.{chunk.page} | {chunk.filename}]\n{chunk.text}")
        if len(parts) == 1:
            return None, False
        out: QueriesOut = await self._ask(self._queries_only, "\n\n".join(parts), self.settings.queries_max_tokens)
        return plausible_queries(self._queries(out, labels, pages), bid.closing, bid.published), True

    async def _deep_read(self, topic: TopicDefinition, labels: list[tuple[str, FileRecord]]) -> str:
        """Notes per ~20 pages over the largest documents, as a last resort for borderline bids."""
        s = self.settings
        files = sorted(((lbl, r) for lbl, r in labels if r.role != "bid_document" and r.pages > 2),
                       key=lambda t: -t[1].pages)
        jobs, budget = [], s.deep_max_pages
        for label, record in files:
            text_pages = [p for p in self.cache.load_text(record) if p.text.strip()][:budget]
            budget -= len(text_pages)
            for i in range(0, len(text_pages), s.deep_pages_per_call):
                batch = text_pages[i : i + s.deep_pages_per_call]
                body = "".join(f"\n--- page {p.page} ---\n{p.text}" for p in batch)[: s.deep_batch_chars]
                header = f"[{label} {record.filename}, pages {batch[0].page}-{batch[-1].page}]"
                jobs.append((header, self._ask(self._notes, f"{topic.render()}\n\n{header}{body}",
                                               s.notes_max_tokens)))
            if budget <= 0:
                break
        results = await asyncio.gather(*(job for _, job in jobs))
        notes = [f"{header}: {out.notes.strip()}" for (header, _), out in zip(jobs, results, strict=True)
                 if out.notes.strip()]
        return "=== Notes from reading the full documents ===\n" + "\n".join(notes) if notes else ""
