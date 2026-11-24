"""Test-only stand-ins for the five agents, with the same method signatures."""
from __future__ import annotations

from datetime import datetime

from gem_agent.agents.chat import Understanding
from gem_agent.agents.matcher import Call, MatchItem
from gem_agent.agents.schemas import (
    Evidence,
    KeyFacts,
    ProbeResult,
    QueriesDeadline,
    ResolvedWindow,
    SearchPlan,
    TenderRequest,
    TopicDefinition,
    TriageOutput,
    Verdict,
)
from gem_agent.agents.searcher import Candidate, SearchResult, SearchSettings
from gem_agent.cache.docs import BidFiles, FileRecord
from gem_agent.index.db import Description
from gem_agent.tools.gem.live import SearchProgress
from gem_agent.tools.gem.models import Bid
from tests.agents.helpers import LOS

NOW = datetime(2026, 11, 16, 13, 40)
WINDOW = ResolvedWindow(published_from=datetime(2026, 11, 2), published_to=NOW, closing_from=NOW)


def bid(b_id: int, title: str = "Loan Origination System for RRBs", closing_day: int = 20) -> Bid:
    return Bid(b_id=b_id, bid_number=f"GEM/2026/B/{b_id}", title=title, ministry="Ministry of Finance",
               department="Department of Financial Services", published=datetime(2026, 11, 10),
               closing=datetime(2026, 11, closing_day, 15), bid_type=1)


class FakeChat:
    def __init__(self, intent="search", topics=(LOS,)):
        self.intent, self.topics, self.calls = intent, list(topics), []

    async def understand(self, user, message, history, *, run_id):
        self.calls.append((user, message, list(history)))
        request = TenderRequest(intent=self.intent, user_text=message, topics=self.topics, window=WINDOW)
        probes = [ProbeResult(meaning=t.meaning, strong=3) for t in self.topics]
        line = "Looking for Loan Origination System tenders published 2–16 Nov (3 matching titles)."
        return Understanding(request=request, probes=probes, progress_line=line if self.topics else "")


class FakePlanner:
    def __init__(self):
        self.notes = []

    async def plan(self, request, topic, note=""):
        self.notes.append(note)
        return SearchPlan(topic_key=topic.key(), text=topic.meaning, terms=["LOS"], **request.window.model_dump())


class FakeSearcher:
    """`results`: the first stage's bids, one list per call (the last repeats). `rest`: the second stage's, likewise.
    `dropped`: likewise, stage-1 bids the title screen rejected; they go to rest_pool, as do bids cut by the cap.
    Like the real Searcher, the second stage needs the progress the first returned and leaves out `exclude`, and a
    result is cut to the cap (`cap` overrides the settings' one) with the number cut reported. `more_pages`: stage 1
    leaves pages for stage 2 (False: every search was read whole on page 1). `searches`: per stage, more search
    progress to report (it may replace "fake"), e.g. failed searches."""

    def __init__(self, results, rest=((),), *, dropped=((),), ignore_exclude=False, cap=150,
                 more_pages=True, searches=None):
        self.results, self.rest, self.dropped = list(results), [list(r) for r in rest], [list(d) for d in dropped]
        self.calls, self.rest_calls = 0, 0
        self.stages, self.excluded, self.ignore_exclude, self.caps = [], [], ignore_exclude, []
        self.settings, self.more_pages, self.searches = SearchSettings(cap=cap), more_pages, dict(searches or {})

    async def search(self, topic: TopicDefinition, plan, *, stage="first", progress=None, exclude=frozenset(),
                     cap=None, on_page=None):
        self.stages.append(stage)
        self.caps.append(cap)
        if stage == "first":
            bids = self.results[min(self.calls, len(self.results) - 1)]
            dropped = self.dropped[min(self.calls, len(self.dropped) - 1)]
            self.calls += 1
        else:
            if not progress or "fake" not in progress:
                raise KeyError("fake")  # live.rest without the first stage's progress
            bids = self.rest[min(self.rest_calls, len(self.rest) - 1)]
            dropped = ()
            self.rest_calls += 1
            self.excluded.append(set(exclude))
            if on_page is not None and bids:  # like live.rest: the live count climbs with the pages
                on_page(len(bids), (cap or 0) * 3)
        bids = [b for b in bids if self.ignore_exclude or b.b_id not in exclude]
        dropped = [b for b in dropped if self.ignore_exclude or b.b_id not in exclude]
        limit = self.settings.cap if cap is None else max(0, cap)
        cut = max(0, len(bids) - limit)
        pages = 2 if self.more_pages else 1
        done = SearchProgress(num_found=len(bids), pages_read=1 if stage == "first" else pages, page_count=pages)
        if stage == "rest" and not progress["fake"].pending:
            done = progress["fake"]  # like live.rest: a search with nothing left keeps its progress (failed, say)
        group = topic.meaning.split(":")[0]
        rest_pool = [Candidate(bid=b, group=group, topic_key=topic.key(), why=["title screen: no", "fake"])
                     for b in dropped]
        rest_pool += [Candidate(bid=b, group=group, topic_key=topic.key(), why=["over the cap", "title words"])
                      for b in bids[limit:]]
        return SearchResult(candidates=[Candidate(bid=b, group=group, topic_key=topic.key(), why=["title words"])
                                        for b in bids[:limit]],
                            rest_pool=rest_pool, pool=(len(bids) + len(dropped)) * 3, over_cap=cut > 0, cut=cut,
                            progress={**(progress or {}), "fake": done, **self.searches.get(stage, {})})


class FakeDescriber:
    """Stores one current fake description per successful call; failed bids keep any older row."""

    def __init__(self, subjects: dict[int, str] | None = None, *, fail=(), has_scope=True):
        self.subjects = dict(subjects or {})
        self.fail, self.has_scope = set(fail), has_scope
        self.model_name = "fake"
        self.prompt_version = "1"
        self.described: list[int] = []
        self.rows: dict[int, Description] = {}

    async def describe(self, bid, files):
        if bid.b_id in self.fail:
            return None
        row = Description(b_id=bid.b_id, doc_hash="fake", subject=self.subjects.get(
            bid.b_id, "Loan Origination System for RRBs"), terms=[], sources=[], has_scope=self.has_scope,
            buyer_org=files.buyer_org, model=self.model_name, prompt_version="1", written_at=NOW)
        self.rows[bid.b_id] = row
        self.described.append(bid.b_id)
        return row

    def known(self, b_ids):
        return {b_id: self.rows[b_id] for b_id in b_ids if b_id in self.rows}

    def stale(self, b_id, files):
        return False


class FakeMatcher:
    def __init__(self, calls: dict[int | tuple[str, int], Call] | None = None, default="match"):
        self.calls, self.default = dict(calls or {}), default
        self.batches: list[list[int]] = []
        self.topics: list[str] = []

    async def match(self, topic, items: list[MatchItem]):
        self.batches.append([item.b_id for item in items])
        self.topics.append(topic.key())
        return {item.b_id: self.calls.get((topic.key(), item.b_id),
                                          self.calls.get(item.b_id, self.default)) for item in items}


class FakeCollector:
    def __init__(self, fail=()):
        self.fail, self.calls = set(fail), []

    async def collect(self, bid, *, force=False, attachments=True):
        self.calls.append((bid.b_id, attachments))
        if bid.b_id in self.fail:
            raise RuntimeError("GeM returned 503 three times")
        files = [FileRecord(role="bid_document", url=f"u{bid.b_id}", filename=f"GeM-Bidding-{bid.b_id}.pdf", size=10,
                            kind="pdf", status="ok", sha256=f"sha{bid.b_id}", path=f"docs/{bid.b_id}/files/a.pdf", pages=5)]
        if attachments:
            files.append(FileRecord(role="attachment", url=f"r{bid.b_id}", filename="RFP.pdf", size=10, kind="pdf",
                                    status="ok", sha256=f"rfp{bid.b_id}", path=f"docs/{bid.b_id}/files/b.pdf", pages=40))
        return BidFiles(b_id=bid.b_id, bid_number=bid.bid_number, fetched_at=NOW, corrigendum_flag=False, files=files,
                        partial=not attachments)


class FakeJudge:
    """Bids whose id is in `no` are clear triage rejections, those in `borderline` are borderline; the rest are relevant."""

    def __init__(self, no=(), borderline=(), question=None):
        self.no, self.borderline, self.judged, self.question = set(no), set(borderline), [], question

    def _verdict(self, topic, bid, relevant, stage, borderline=False):
        return Verdict(b_id=bid.b_id, bid_number=bid.bid_number, topic_key=topic.key(), doc_hash="h", relevant=relevant,
                       borderline=borderline, question=(self.question or f"Does bid {bid.b_id} count for you?") if borderline else "",
                       confidence=0.9, stage=stage, triage="maybe", reason="RFP asks for an LOS." if relevant else "Lockers.",
                       evidence=[Evidence(quote="implement a Loan Origination System", file="RFP.pdf", page=3, verified=True)],
                       key_facts=KeyFacts(emd="Rs 5 lakh", min_turnover=None, contract_period=None, estimated_value=None,
                                          scope_summary=None),
                       queries=QueriesDeadline(when=datetime(2026, 11, 18, 17, 0), source="documents",
                                               quote="Last date for submission of Queries : 18th November 2026",
                                               file="RFP.pdf", page=1, verified=True) if relevant else None,
                       model="fake", decided_at=NOW)

    async def screen(self, topic, bid, files):
        if bid.b_id in self.no:
            return self._verdict(topic, bid, False, "triage"), TriageOutput(decision="clearly_no", reason="lockers")
        return None, TriageOutput(decision="maybe", reason="title")

    async def judge(self, topic, bid, files, triage=None):
        self.judged.append(bid.b_id)
        if bid.b_id in self.borderline:
            return self._verdict(topic, bid, False, "judge", borderline=True)
        return self._verdict(topic, bid, True, "judge")


class BlockingCollector(FakeCollector):
    """Never finishes collecting, so a run can be observed while it is running."""

    def __init__(self):
        super().__init__()
        import asyncio
        self.release = asyncio.Event()

    async def collect(self, bid, *, force=False, attachments=True):
        await self.release.wait()
        return await super().collect(bid, force=force, attachments=attachments)


class FakeRulings:
    """`answers` maps a borderline question to the match to return; other questions match nothing.
    `match_first` maps a question to a decision: the first fact offered is matched with it (none offered, no match).
    `fail_write` makes only write_fact raise."""

    def __init__(self, answers=None, fail=False, match_first=None, fail_write=False):
        self.answers, self.fail, self.resolved, self.written = dict(answers or {}), fail, [], []
        self.match_first, self.fail_write = dict(match_first or {}), fail_write

    async def resolve(self, topic, question, reason, facts):
        self.resolved.append(question)
        if self.fail:
            raise RuntimeError("model down")
        if question in self.match_first and facts:
            from gem_agent.agents.rulings import RulingMatch
            return RulingMatch(fact_id=facts[0].id, decision=self.match_first[question])
        return self.answers.get(question)

    async def write_fact(self, topic, question, decision):
        self.written.append((question, decision))
        if self.fail or self.fail_write:
            raise RuntimeError("model down")
        return f"fact for {question} ({decision})"
