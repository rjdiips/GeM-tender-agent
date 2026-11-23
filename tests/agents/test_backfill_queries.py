"""Backfill of the queries date for older results."""
from datetime import datetime

import pytest

from gem_agent.agents.judge import QUERIES_INSTRUCTIONS, Judge, doc_hash
from gem_agent.agents.schemas import Evidence, Verdict
from gem_agent.cache.docs import DocCache
from gem_agent.cache.verdicts import VerdictCache
from gem_agent.graph.store import Outcome, RunRecord, RunStore, Ruling
from gem_agent.jobs.backfill_queries import backfill_queries
from gem_agent.tools.gem.models import Bid
from tests.agents.helpers import ScriptedLLM, bid_files

COVER = "Last date for submission of Queries : 20th November 2026, 05.00 PM Technical bid Opening : 8th December 2026"
PRE_BID = "Pre Bid Detail(s)\nPre-Bid Date and Time\nPre-Bid Venue\n18-11-2026 11:00:00\nOnline"
ANSWER = {"kind": "queries_deadline", "quote": "Last date for submission of Queries : 20th November 2026, 05.00 PM",
          "year": 2026, "month": 11, "day": 20, "hour": 17, "minute": 0}
CLOSING = datetime(2026, 12, 7, 16, 0)


@pytest.fixture
def cache(tmp_path):
    return DocCache(tmp_path / "cache")


def seed(cache, store, verdicts, run_id, b_id, pages_bid, pages_rfp, *, relevant, stage, restored=False):
    files = bid_files(cache, b_id, [("bid.pdf", "bid_document", pages_bid), ("rfp.pdf", "attachment", pages_rfp)])
    cache.save(files)
    bid = Bid(b_id=b_id, bid_number=f"GEM/2026/B/{b_id}", title="IT services", closing=CLOSING, bid_type=1)
    v = Verdict(b_id=b_id, bid_number=bid.bid_number, topic_key="t", doc_hash=doc_hash(files), relevant=relevant,
                confidence=0.9, stage=stage, triage="maybe", reason="r",
                evidence=[Evidence(quote="q", file="rfp.pdf", page=2, verified=True)], model="m",
                decided_at=datetime(2026, 11, 16))
    verdicts.put(v)
    if not store.exists(run_id):
        store.create(RunRecord(run_id=run_id, user="raj", title="t", request_text="t", created=datetime(2026, 11, 16)))
    store.save_outcome(run_id, Outcome(b_id=b_id, group="g", bid=bid, files=files.files, verdict=v, restored=restored))


async def test_backfill_fills_old_results_once_per_bid_and_only_where_it_matters(cache, tmp_path):
    store, verdicts = RunStore(tmp_path / "runs"), VerdictCache(cache.root)
    seed(cache, store, verdicts, "r1", 1, ["Bid 1", "terms"], [COVER, "scope"], relevant=True, stage="judge")
    seed(cache, store, verdicts, "r2", 1, ["Bid 1", "terms"], [COVER, "scope"], relevant=True, stage="judge")
    seed(cache, store, verdicts, "r1", 2, ["Bid 2", PRE_BID], ["scope"], relevant=False, stage="triage")
    seed(cache, store, verdicts, "r1", 3, ["Bid 3", "terms"], [COVER, "scope"], relevant=False, stage="judge")
    seed(cache, store, verdicts, "r1", 4, ["Bid 4", "terms"], [COVER, "scope"], relevant=False, stage="judge",
         restored=True)
    llm = ScriptedLLM({QUERIES_INSTRUCTIONS: [ANSWER]})
    judge = Judge(llm.model(), cache, verdicts)

    report = await backfill_queries(judge, cache, verdicts, store)

    assert llm.count(QUERIES_INSTRUCTIONS) == 2  # bid 1 once for both runs, bid 4 (restored); not 2 or 3
    assert "submission of Queries" in llm.calls[0][1]
    for run_id in ("r1", "r2"):
        q = store.outcomes(run_id)[1].verdict.queries
        assert q.when == datetime(2026, 11, 20, 17, 0) and q.verified and q.page == 1
    gem = store.outcomes("r1")[2].verdict.queries
    assert gem.source == "gem_pre_bid" and gem.when == datetime(2026, 11, 18, 11, 0)
    assert store.outcomes("r1")[3].verdict.queries is None
    assert verdicts.get(1, "t", store.outcomes("r1")[1].verdict.doc_hash).queries is not None
    assert report.filled == 4 and report.model_calls == 2

    again = await backfill_queries(judge, cache, verdicts, store)
    assert llm.count(QUERIES_INSTRUCTIONS) == 2 and again.filled == 0  # bid 3 has no model call to retry


async def test_a_backfilled_date_after_closing_is_dropped(cache, tmp_path):
    store, verdicts = RunStore(tmp_path / "runs"), VerdictCache(cache.root)
    seed(cache, store, verdicts, "r1", 1, ["Bid 1", "terms"], [COVER, "scope"], relevant=True, stage="judge")
    late = {**ANSWER, "month": 12, "day": 7, "hour": 16}
    judge = Judge(ScriptedLLM({QUERIES_INSTRUCTIONS: [late]}).model(), cache, verdicts)
    await backfill_queries(judge, cache, verdicts, store)
    assert store.outcomes("r1")[1].verdict.queries is None


async def test_dry_run_writes_nothing(cache, tmp_path):
    store, verdicts = RunStore(tmp_path / "runs"), VerdictCache(cache.root)
    seed(cache, store, verdicts, "r1", 2, ["Bid 2", PRE_BID], ["scope"], relevant=True, stage="judge")
    llm = ScriptedLLM({QUERIES_INSTRUCTIONS: [ANSWER]})
    report = await backfill_queries(Judge(llm.model(), cache, verdicts), cache, verdicts, store, dry_run=True)
    assert report.filled == 1 and store.outcomes("r1")[2].verdict.queries is None



async def test_redo_reads_document_dates_again_and_keeps_gems(cache, tmp_path):
    store, verdicts = RunStore(tmp_path / "runs"), VerdictCache(cache.root)
    seed(cache, store, verdicts, "r1", 1, ["Bid 1", "terms"], [COVER, "scope"], relevant=True, stage="judge")
    seed(cache, store, verdicts, "r1", 2, ["Bid 2", PRE_BID], ["scope"], relevant=True, stage="judge")
    await backfill_queries(Judge(ScriptedLLM({QUERIES_INSTRUCTIONS: [ANSWER]}).model(), cache, verdicts), cache,
                           verdicts, store)
    wrong = {**ANSWER, "kind": "bid_submission"}
    llm = ScriptedLLM({QUERIES_INSTRUCTIONS: [wrong]})
    report = await backfill_queries(Judge(llm.model(), cache, verdicts), cache, verdicts, store, redo_documents=True)
    assert llm.count(QUERIES_INSTRUCTIONS) == 1 and report.cleared == 1
    out = store.outcomes("r1")
    assert out[1].verdict.queries is None and out[2].verdict.queries.source == "gem_pre_bid"
    assert verdicts.get(1, "t", out[1].verdict.doc_hash).queries is None


async def test_a_ruling_made_during_the_backfill_survives_it(cache, tmp_path):
    store, verdicts = RunStore(tmp_path / "runs"), VerdictCache(cache.root)
    seed(cache, store, verdicts, "r1", 1, ["Bid 1", "terms"], [COVER, "scope"], relevant=True, stage="judge")
    judge = Judge(ScriptedLLM({QUERIES_INSTRUCTIONS: [ANSWER]}).model(), cache, verdicts)

    class RulingDuringModelCall:
        async def find_queries(self, *args, **kwargs):
            def rule(all_):  # the user rules while the slow model call is under way
                ruling = Ruling(decision="not", by="you", fact_id="f", fact="x", at=datetime(2026, 11, 18))
                all_[1] = all_[1].model_copy(update={"ruling": ruling, "undone": True})
                return [1]
            store.update_outcomes("r1", rule)
            return await judge.find_queries(*args, **kwargs)

    await backfill_queries(RulingDuringModelCall(), cache, verdicts, store)

    o = store.outcomes("r1")[1]
    assert o.verdict.queries is not None  # the backfill still wrote its date
    assert o.ruling is not None and o.ruling.decision == "not" and o.undone
