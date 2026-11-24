"""Typed agent inputs/outputs: TopicDefinition, triage and judge answers, Verdict."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta
from typing import Literal

from pydantic import BaseModel, Field


class TopicDefinition(BaseModel):
    """What the user means, written by the Chat agent before any search."""

    user_text: str
    meaning: str
    domain: str = ""
    match_terms: list[str] = []
    acronyms: dict[str, str] = {}
    related_but_not_enough: list[str] = []
    not_this: list[str] = []
    buyers: list[str] = []  # buyers the user asked for ("banks"); the Judge checks them against the documents
    chosen_because: str = ""
    other_meanings_searched: list[str] = []

    def key(self) -> str:
        """Stable hash of everything that can change a verdict."""
        data = self.model_dump(exclude={"chosen_because", "other_meanings_searched"})
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]

    def render(self) -> str:
        lines = [f"Topic asked for: {self.user_text}", f"Meaning: {self.meaning}"]
        if self.domain:
            lines.append(f"Domain: {self.domain}")
        if self.acronyms:
            lines.append("Acronyms: " + "; ".join(f"{k} = {v}" for k, v in self.acronyms.items()))
        if self.match_terms:
            lines.append("Terms that indicate it: " + "; ".join(self.match_terms))
        if self.related_but_not_enough:
            lines.append("Related but NOT enough on their own: " + "; ".join(self.related_but_not_enough))
        if self.not_this:
            lines.append("NOT this (look-alikes): " + "; ".join(self.not_this))
        if self.buyers:
            lines.append("Buyers the user wants: " + "; ".join(self.buyers) +
                         " (a tender from another kind of buyer is not relevant; the documents name the buyer)")
        return "\n".join(lines)


# --- what the model fills in --------------------------------------------------------------------------------------


class TriageOutput(BaseModel):
    decision: Literal["clearly_no", "maybe", "clearly_yes"]
    reason: str = Field(max_length=300, description="one short sentence")


class QuoteOut(BaseModel):
    quote: str = Field(max_length=400, description="exact words copied from the passage, 8-40 words")
    file: str = Field(max_length=100, description="the passage label, e.g. F2")
    page: int


class KeyFacts(BaseModel):
    # Required (nullable): a field with a default is one the schema-enforced model may leave out.
    emd: str | None = Field(max_length=100, description="earnest money deposit amount, if stated")
    min_turnover: str | None = Field(max_length=100, description="minimum average annual turnover required of bidders")
    contract_period: str | None = Field(max_length=100)
    estimated_value: str | None = Field(max_length=100)
    scope_summary: str | None = Field(max_length=400, description="one or two sentences on what is being bought")


class QueriesOut(BaseModel):
    """The last date for bidders' queries, as numbers: asked for as an ISO string, the model copied the document's
    "20th November 2026, 05.00 PM". `kind` makes it say what date it quoted: asked only to avoid
    the closing date, it still gave bid end and submission dates."""

    kind: Literal["queries_deadline", "pre_bid_meeting", "bid_submission", "bid_opening", "other", "none"] = Field(
        description="what the quoted date is: the last date for queries or clarifications, the pre-bid meeting, the "
                    "bid submission or end date, the bid opening, something else, or none found")
    quote: str | None = Field(max_length=300, description="the exact words that state the date, copied from the "
                              "passage, including the date itself; null if not stated")
    year: int | None = Field(ge=2000, le=2100)
    month: int | None = Field(ge=1, le=12)
    day: int | None = Field(ge=1, le=31)
    hour: int | None = Field(ge=0, le=23, description="24-hour clock; null if no time is given")
    minute: int | None = Field(ge=0, le=59)


Decision = Literal["relevant", "borderline", "not_relevant"]


class JudgeOutput(BaseModel):
    decision: Decision
    question: str = Field(max_length=200, description="for a borderline decision, the one question the reader must "
                          "answer; empty unless the decision is borderline")
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(max_length=400, description="one sentence a sales person can read")
    # Required, so the enforced JSON schema can't drop them. A "no" quotes what the tender is actually about.
    evidence: list[QuoteOut] = Field(min_length=1, max_length=3, description="1-3 quotes showing the decision")
    key_facts: KeyFacts
    queries: QueriesOut


class NotesOutput(BaseModel):
    notes: str = Field(max_length=900, description="what these pages say about the topic, max 120 words; empty if nothing")
    pages: list[int] = Field(description="pages that mention the topic")


# --- what we store ------------------------------------------------------------------------------------------------


class Evidence(BaseModel):
    quote: str
    file: str  # filename
    page: int
    verified: bool  # the quote was found in that page's text


class QueriesDeadline(BaseModel):
    """The last date for bidders' queries: GeM's own pre-bid date when its bid document has one, else the date the
    Judge read in the buyer's documents (an RFP's cover page), with the words it read it from."""

    when: datetime
    source: Literal["gem_pre_bid", "documents"]
    # GeM's pre-bid date is when the pre-bid meeting is held; an RFP may set an earlier last date for queries,
    # so the UI names which one it shows
    kind: Literal["queries_deadline", "pre_bid_meeting"] = "queries_deadline"
    quote: str
    file: str  # filename
    page: int
    verified: bool  # the quote was found in that page's text


class Verdict(BaseModel):
    b_id: int
    bid_number: str
    topic_key: str
    doc_hash: str
    relevant: bool
    borderline: bool = False  # close to the topic; whether it counts is the reader's call (older verdicts have none)
    question: str = ""  # what to ask the reader when borderline
    confidence: float
    stage: Literal["triage", "judge", "deep"]
    triage: str
    reason: str
    evidence: list[Evidence] = []
    key_facts: KeyFacts | None = None
    queries: QueriesDeadline | None = None  # older verdicts have none
    needs_review: bool = False
    model: str
    decided_at: datetime


# --- understanding a request ----------------------------------------------------------------


class ResolvedWindow(BaseModel):
    published_from: datetime | None = None
    published_to: datetime | None = None
    closing_from: datetime | None = None
    closing_to: datetime | None = None

    def describe(self) -> str:
        def span(a: datetime, b: datetime) -> str:
            if (a.year, a.month) == (b.year, b.month):
                return f"{a.day}–{b.day} {b:%b}"
            return f"{a.day} {a:%b} – {b.day} {b:%b}"

        if self.published_from and self.published_to:
            return f"published {span(self.published_from, self.published_to)}"
        if self.published_to:  # a named end date with no named start date is still a bounded window
            return f"published until {self.published_to:%d %b}"
        if self.closing_from and self.closing_to:
            return f"closing {span(self.closing_from, self.closing_to)}"
        if self.closing_from:
            return f"closing after {self.closing_from.day} {self.closing_from:%b}"
        if self.closing_to:
            return f"closing until {self.closing_to.day} {self.closing_to:%b}"
        return "open now"


class DateWindow(BaseModel):
    """What the user said about dates. "Within N weeks" means published unless they say closing."""

    kind: Literal["open", "published", "closing"] = "open"
    last_days: int | None = Field(None, description="published within the last N days")
    next_days: int | None = Field(None, description="closing within the next N days")
    from_date: date | None = None
    to_date: date | None = None

    def resolve(self, now: datetime, *, explicit_dates: bool = False) -> ResolvedWindow:
        kind = self.kind
        if kind == "open":  # the model sometimes leaves kind at "open" while filling the numbers
            if self.last_days is not None:
                kind = "published"
            elif self.next_days or self.to_date:
                kind = "closing"
        day = datetime.combine(now.date(), time())
        end = lambda d: datetime.combine(d, time(23, 59, 59))
        start = lambda d: datetime.combine(d, time())
        if kind == "published":
            if explicit_dates and (self.from_date or self.to_date):  # dates the user named win over last_days
                return ResolvedWindow(published_from=start(self.from_date) if self.from_date else None,
                                      published_to=end(self.to_date) if self.to_date else now, closing_from=now)
            if self.from_date and self.last_days is None:  # the user's own number wins over dates the model added
                return ResolvedWindow(published_from=start(self.from_date),
                                      published_to=end(self.to_date) if self.to_date else now, closing_from=now)
            days = 14 if self.last_days is None else self.last_days
            return ResolvedWindow(published_from=day - timedelta(days=days), published_to=now, closing_from=now)
        if kind == "closing":
            if (explicit_dates and (self.from_date or self.to_date)) or (self.to_date and not self.next_days):
                return ResolvedWindow(closing_from=max(now, start(self.from_date)) if self.from_date else now,
                                      closing_to=end(self.to_date) if self.to_date else None)
            return ResolvedWindow(closing_from=now, closing_to=end(now.date() + timedelta(days=self.next_days or 7)))
        return ResolvedWindow(closing_from=now)


# --- user memory --------------------------------------------------------------------------------------


class MemoryFact(BaseModel):
    id: str
    fact: str
    evidence: str
    source: Literal["stated", "learned"]
    since: date
    runs: list[str] = []
    strength: int = 0
    active: bool = False
    removed: bool = False
    kind: Literal["general", "ruling"] = "general"
    topic: str = ""  # a ruling's topic short name


class MemoryChange(BaseModel):
    """One change the post-run memory step proposes."""

    action: Literal["add", "support", "weaken", "remove", "update"]
    fact: str | None = Field(max_length=200, description="the fact text, for add and update")
    fact_id: str | None = Field(max_length=50, description="an existing fact's id, for support / weaken / remove / update")
    evidence: str = Field(max_length=300, description="what in this run shows it")


class ProbeResult(BaseModel):
    """How a meaning fares against the local index of open-bid titles."""

    meaning: str
    strong: int  # distinct page-1 bids in the date window whose search matched the meaning's terms (word rules of 5.3)
    top_titles: list[str] = []


class TenderRequest(BaseModel):
    intent: Literal["search", "refine", "show_rejected", "memory_list", "memory_forget", "other"]
    user_text: str
    topics: list[TopicDefinition] = []  # likeliest meaning first; two when both stayed plausible
    window: ResolvedWindow = Field(default_factory=ResolvedWindow)
    buyer_hints: list[str] = []
    buyer_only: bool = False  # "only …": buyer hints become hard filters


class SearchPlan(BaseModel):
    """What the Searcher runs for one topic: live title searches plus optional category, buyer and bid-type searches."""

    topic_key: str
    text: str  # semantic query: the expanded meaning, never a bare acronym
    terms: list[str]  # word-search terms
    published_from: datetime | None = None
    published_to: datetime | None = None
    closing_from: datetime | None = None
    closing_to: datetime | None = None
    ministries: list[str] = []  # hard filters: only when the user said "only …"
    departments: list[str] = []
    prefer_ministries: list[str] = []  # buyer hints otherwise: ranked higher, nothing dropped
    prefer_departments: list[str] = []
    bid_types: list[int] = []
    categories: list[str] = []  # exact site values for live searches
    reasons: str = ""
