import asyncio
import json
from datetime import datetime

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from gem_agent.agents.judge import doc_hash
from gem_agent.cache.docs import DocCache
from gem_agent.index.db import BidIndex, Description
from gem_agent.index.describe import DESCRIBE_INSTRUCTIONS, Describer, RefreshReport, refresh_descriptions
from gem_agent.tools.gem.models import Bid
from tests.agents.helpers import ScriptedLLM, bid_files
from tests.index.helpers import row

TITLE = "Hiring of Agency for IT Projects- Milestone basis"


def setup(tmp_path, *, title=TITLE, b_ids=(1,)):
    index = BidIndex(tmp_path / "bids.sqlite")
    at = datetime(2026, 11, 10)
    bids = [Bid.from_listing(row(b_id, at, datetime(2026, 11, 28), title)) for b_id in b_ids]
    index.upsert(bids, seen_at=at)
    return index, DocCache(tmp_path / "docs"), bids


def copied_card(index, bid):
    description = Description(
        b_id=bid.b_id,
        doc_hash="",
        subject="Old card",
        terms=["old term"],
        sources=[],
        has_scope=False,
        buyer_org=None,
        model="old-model",
        prompt_version="card",
        written_at=datetime(2026, 11, 10),
    )
    index.put_description(description)
    return description


async def test_a_description_is_written_from_the_bid_document_and_scope_and_stored(tmp_path):
    index, cache, [bid] = setup(tmp_path)
    files = bid_files(cache, bid.b_id, [
        ("GeM-Bidding.pdf", "bid_document", ["Enterprise KYC and AML for NABARD"]),
        ("RFP.pdf", "attachment", ["3. Scope of Work\nKYC, AML and transaction monitoring for NABARD"]),
    ])
    llm = ScriptedLLM({DESCRIBE_INSTRUCTIONS: [{
        "subject": "Enterprise KYC and AML for NABARD",
        "terms": ["KYC", "AML", "transaction monitoring"],
    }]})
    describer = Describer(llm.model(), cache, index, model_name="scripted")

    d = await describer.describe(bid, files)

    assert d is not None
    assert d.subject == "Enterprise KYC and AML for NABARD" and d.has_scope
    assert d.sources == ["GeM-Bidding.pdf", "RFP.pdf"] and d.doc_hash == doc_hash(files)
    assert (d.model, d.prompt_version) == ("scripted", "1") and index.description(bid.b_id) == d
    assert describer.known([bid.b_id, 999]) == {bid.b_id: d}
    assert "Scope of Work" in llm.calls[0][1]


async def test_a_failed_description_is_not_stored(tmp_path):
    index, cache, [bid] = setup(tmp_path)
    files = bid_files(cache, bid.b_id, [("GeM-Bidding.pdf", "bid_document", ["Bid details"])])
    llm = ScriptedLLM({DESCRIBE_INSTRUCTIONS: [{"not": "the schema"}]})
    describer = Describer(llm.model(), cache, index, model_name="scripted")

    assert await describer.describe(bid, files) is None
    assert index.description(bid.b_id) is None
    assert llm.count(DESCRIBE_INSTRUCTIONS) == 3  # the initial call plus two retries


async def test_a_description_restating_the_title_is_stored_empty(tmp_path):
    index, cache, [bid] = setup(tmp_path)
    files = bid_files(cache, bid.b_id, [("GeM-Bidding.pdf", "bid_document", ["Generic category only"])])
    llm = ScriptedLLM({DESCRIBE_INSTRUCTIONS: [{
        "subject": TITLE,
        "terms": ["IT projects", "milestone basis"],
    }]})
    describer = Describer(llm.model(), cache, index, model_name="scripted")

    d = await describer.describe(bid, files)

    assert d is not None and index.description(bid.b_id) == d
    assert d.subject == "" and d.terms == [] and not d.usable()


async def test_stale_only_when_a_row_exists_and_the_documents_changed(tmp_path):
    index, cache, [bid] = setup(tmp_path)
    files = bid_files(cache, bid.b_id, [("GeM-Bidding.pdf", "bid_document", ["Bid details"])])
    llm = ScriptedLLM({DESCRIBE_INSTRUCTIONS: [{"subject": "KYC platform", "terms": []}]})
    describer = Describer(llm.model(), cache, index, model_name="scripted")

    assert not describer.stale(bid.b_id, files)
    d = await describer.describe(bid, files)
    assert d is not None and not describer.stale(bid.b_id, files)

    changed = bid_files(cache, bid.b_id, [("GeM-Bidding.pdf", "bid_document", ["Changed bid details"])])
    assert describer.stale(bid.b_id, changed)

    index.put_description(d.model_copy(update={"doc_hash": "", "prompt_version": "card"}))
    assert describer.stale(bid.b_id, files)


def test_describer_rejects_less_than_one_parallel(tmp_path):
    index, cache, _ = setup(tmp_path)
    llm = ScriptedLLM({DESCRIBE_INSTRUCTIONS: [{"subject": "KYC platform", "terms": []}]})

    for parallel in (0, -1):
        with pytest.raises(ValueError, match="parallel"):
            Describer(llm.model(), cache, index, model_name="scripted", parallel=parallel)


async def test_describing_never_runs_more_than_three_model_calls_at_once(tmp_path):
    index, cache, bids = setup(tmp_path, b_ids=range(1, 7))
    files = [bid_files(cache, bid.b_id, [("GeM-Bidding.pdf", "bid_document", ["Enterprise KYC platform"])])
             for bid in bids]
    active = peak = 0

    async def reply(messages, info):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return ModelResponse(parts=[TextPart(json.dumps({"subject": "Enterprise KYC platform", "terms": []}))])

    describer = Describer(FunctionModel(reply, model_name="scripted"), cache, index, model_name="scripted", parallel=6)

    descriptions = await asyncio.gather(*(describer.describe(bid, f) for bid, f in zip(bids, files, strict=True)))

    assert all(d is not None for d in descriptions)
    assert peak == 3


async def test_refresh_reads_only_cached_documents(tmp_path):
    index, cache, bids = setup(tmp_path, b_ids=(1, 2))
    for bid in bids:
        copied_card(index, bid)
    cache.save(bid_files(cache, 1, [("GeM-Bidding.pdf", "bid_document", ["Enterprise KYC platform"])]))
    llm = ScriptedLLM({DESCRIBE_INSTRUCTIONS: [{"subject": "Enterprise KYC platform", "terms": ["KYC"]}]})
    describer = Describer(llm.model(), cache, index, model_name="scripted")

    report = await refresh_descriptions(describer, index, cache, limit=10)

    assert report == RefreshReport(described=1, no_documents=1, failed=0)
    assert index.description(1).subject == "Enterprise KYC platform"
    assert index.description(2).prompt_version == "card"
    assert llm.count(DESCRIBE_INSTRUCTIONS) == 1


async def test_refresh_counts_an_unreadable_manifest_as_failed_and_keeps_going(tmp_path):
    index, cache, bids = setup(tmp_path, b_ids=(1, 2))
    old = {bid.b_id: copied_card(index, bid) for bid in bids}
    cache.save(bid_files(cache, 1, [("GeM-Bidding.pdf", "bid_document", ["Enterprise KYC platform"])]))
    manifest = cache.root / "docs/2/manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{", encoding="utf-8")
    llm = ScriptedLLM({DESCRIBE_INSTRUCTIONS: [{"subject": "Enterprise KYC platform", "terms": ["KYC"]}]})
    describer = Describer(llm.model(), cache, index, model_name="scripted")

    report = await refresh_descriptions(describer, index, cache, limit=10)

    assert report == RefreshReport(described=1, no_documents=0, failed=1)
    assert index.description(1).subject == "Enterprise KYC platform"
    assert index.description(2) == old[2]
    assert llm.count(DESCRIBE_INSTRUCTIONS) == 1


async def test_refresh_drains_siblings_before_propagating_an_unexpected_failure(tmp_path):
    index, cache, bids = setup(tmp_path, b_ids=(1, 2))
    for bid in bids:
        cache.save(bid_files(cache, bid.b_id, [("GeM-Bidding.pdf", "bid_document", ["Enterprise KYC platform"])]))
    started, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class BrokenDescriber:
        model_name = "scripted"

        async def describe(self, bid, files):
            if bid.b_id == 1:
                await started.wait()
                raise RuntimeError("unexpected describer failure")
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    try:
        with pytest.raises(RuntimeError, match="unexpected describer failure"):
            await asyncio.wait_for(refresh_descriptions(BrokenDescriber(), index, cache, limit=10), timeout=1)
        assert cancelled.is_set()
    finally:
        release.set()
        await asyncio.sleep(0)
