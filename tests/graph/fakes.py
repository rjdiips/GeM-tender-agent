"""Test-only stand-ins for the five agents, with the same method signatures."""
from __future__ import annotations

from datetime import datetime

from gem_agent.agents.chat import Understanding
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
from gem_agent.agents.searcher import Candidate, SearchResult
from gem_agent.cache.docs import BidFiles, FileRecord
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
        probes = [ProbeResult(meaning=t.meaning, window_count=36104, strong=3) for t in self.topics]
        line = "Looking for Loan Origination System tenders published 2–16 Nov (36,104 open bids in that window; 3 look relevant from their titles)."
        return Understanding(request=request, probes=probes, progress_line=line if self.topics else "")


class FakePlanner:
    def __init__(self):
        self.notes = []

    async def plan(self, request, topic, note=""):
        self.notes.append(note)
        return SearchPlan(topic_key=topic.key(), text=topic.meaning, terms=["LOS"], **request.window.model_dump())


class FakeSearcher:
    def __init__(self, results):  # list of lists of bids, one per call
        self.results, self.calls = list(results), 0

    async def search(self, topic: TopicDefinition, plan):
        bids = self.results[min(self.calls, len(self.results) - 1)]
        self.calls += 1
        return SearchResult(candidates=[Candidate(bid=b, group=topic.meaning.split(":")[0], why=["title words"]) for b in bids],
                            pool=len(bids) * 3)


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

    def __init__(self, no=(), borderline=()):
        self.no, self.borderline, self.judged = set(no), set(borderline), []

    def _verdict(self, topic, bid, relevant, stage, borderline=False):
        return Verdict(b_id=bid.b_id, bid_number=bid.bid_number, topic_key=topic.key(), doc_hash="h", relevant=relevant,
                       borderline=borderline, question=f"Does bid {bid.b_id} count for you?" if borderline else "",
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
    """`answers` maps a borderline question to the match to return; other questions match nothing."""

    def __init__(self, answers=None, fail=False):
        self.answers, self.fail, self.resolved, self.written = dict(answers or {}), fail, [], []

    async def resolve(self, topic, question, reason, facts):
        self.resolved.append(question)
        if self.fail:
            raise RuntimeError("model down")
        return self.answers.get(question)

    async def write_fact(self, topic, question, decision):
        self.written.append((question, decision))
        if self.fail:
            raise RuntimeError("model down")
        return f"fact for {question} ({decision})"
