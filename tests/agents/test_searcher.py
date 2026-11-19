import re
from datetime import datetime

import pytest

from gem_agent.agents.schemas import SearchPlan
from gem_agent.agents.searcher import BASE_SPECS, SCREEN_INSTRUCTIONS, Searcher, SearchSettings, search_specs
from gem_agent.index.db import BidIndex
from gem_agent.tools.gem.live import LiveSearcher
from gem_agent.tools.gem.models import Bid
from gem_agent.tools.http_client import BidplusSession, ClientPool
from tests.agents.helpers import LOS, ScriptedLLM
from tests.index.helpers import row
from tests.tools.live_helpers import FakeSearchSite

B = "https://bidplus.gem.gov.in"
T = datetime(2026, 11, 15)
CATEGORY = "services_home_itse_hi12433188"


def at(day, hour=12):
    return datetime(2026, 11, day, hour)


def mk(b_id, title, department="Department of Revenue", closing=at(20), published=at(10), **kw):
    return row(b_id, published, closing, title, department=department, **kw)


ROWS = {
    1: mk(1, "Supply of Loan Origination System for RRBs", "Department of Financial Services", at(20)),
    2: mk(2, "LOST property lockers", closing=at(19)),
    3: mk(3, "Office chairs", closing=at(18)),
    4: mk(4, "Digital lending platform with loan origination workflow", "REGIONAL RURAL BANKS", at(25)),
    5: mk(5, "Hiring of Agency for IT Projects- Milestone basis", "Department of Financial Services", at(17)),
}


def plan(**kw):
    base = {"topic_key": LOS.key(), "text": LOS.meaning, "terms": ["Loan Origination System", "LOS", "loan origination"],
            "categories": [CATEGORY], "published_from": at(2, 0), "published_to": at(16), "closing_from": at(16)}
    return SearchPlan(**(base | kw))


def keep(*ids_by_position):
    return {SCREEN_INSTRUCTIONS: [{"keep": list(ids_by_position)}]}


def screened(llm):
    return llm.calls[0][1] if llm.calls else ""


def keep_all():
    return ScriptedLLM({SCREEN_INSTRUCTIONS: lambda p: {"keep": list(range(40))}})


def ids(found):
    return {c.bid.b_id for c in found.candidates}


@pytest.fixture
def bid_cache():
    idx = BidIndex(":memory:")
    yield idx
    idx.close()


def build(gem, clock, bid_cache, llm, by_query, **settings):
    site = FakeSearchSite(by_query)
    gem.add("GET", f"{B}/advance-search", "advance_search.html.gz")
    gem.add("POST", f"{B}/all-bids-data", site)
    gem.add("POST", f"{B}/search-bids", site)
    live = LiveSearcher(BidplusSession(ClientPool(transport=gem.transport(), clock=clock, sleep=clock.sleep)))
    return Searcher(llm.model(), bid_cache, live, settings=SearchSettings(**settings) if settings else None), site


def the_site(*row_ids):
    return {f"category|{CATEGORY}": [ROWS[i] for i in row_ids]}


def test_search_specs_start_with_the_base_searches_and_drop_repeats():
    specs = search_specs(plan(terms=["LOS", "LOS"], ministries=["Ministry of Finance"],
                              departments=["REGIONAL RURAL BANKS"], prefer_departments=["REGIONAL RURAL BANKS"]))
    assert specs[: len(BASE_SPECS)] == BASE_SPECS
    assert [s.key for s in specs[len(BASE_SPECS):]] == ["exact|Ministry of Finance", "exact|REGIONAL RURAL BANKS", "fullText|LOS"]
    assert len({s.key for s in specs}) == len(specs)


async def test_base_searches_run_even_when_the_plan_has_no_categories_or_buyers(gem, clock, bid_cache):
    searcher, site = build(gem, clock, bid_cache, keep_all(), {})
    await searcher.search(LOS, plan(terms=["LOS"], categories=[]))
    assert {k for k, _ in site.served} >= {s.key for s in BASE_SPECS}


async def test_first_stage_reads_page_one_and_rest_reads_the_remaining_pages(gem, clock, bid_cache):
    many = [mk(100 + i, f"Loan origination item {i}") for i in range(25)]
    searcher, site = build(gem, clock, bid_cache, keep_all(), {"fullText|loan origination": many})
    p = plan(categories=[], published_from=None, published_to=None, closing_from=None)
    first = await searcher.search(LOS, p, stage="first")
    assert {pg for _, pg in site.served} == {1}
    await searcher.search(LOS, p, stage="rest", progress=first.progress, exclude={c.bid.b_id for c in first.candidates})
    assert 2 in {pg for k, pg in site.served if k == "fullText|loan origination"}


async def test_the_second_stage_returns_only_bids_the_first_did_not(gem, clock, bid_cache):
    many = [mk(100 + i, f"Loan origination item {i}") for i in range(25)]
    searcher, _ = build(gem, clock, bid_cache, keep_all(), {"fullText|loan origination": many})
    p = plan(categories=[], published_from=None, published_to=None, closing_from=None)
    first = await searcher.search(LOS, p, stage="first")
    seen = {c.bid.b_id for c in first.candidates}
    second = await searcher.search(LOS, p, stage="rest", progress=first.progress, exclude=seen)
    assert second.candidates and not ({c.bid.b_id for c in second.candidates} & seen)
    assert second.progress["fullText|loan origination"].pages_read == 3


async def test_vague_titles_without_a_card_are_never_dropped(gem, clock, bid_cache):
    llm = ScriptedLLM({SCREEN_INSTRUCTIONS: [{"keep": []}]})
    searcher, _ = build(gem, clock, bid_cache, llm, the_site(3, 5))
    found = await searcher.search(LOS, plan())
    assert ids(found) == {5}
    assert "vague title" in found.candidates[0].why[0]


async def test_a_vague_title_with_a_card_is_screened_by_its_card(gem, clock, bid_cache):
    bid_cache.upsert([Bid.from_listing(ROWS[5])], seen_at=T)
    bid_cache.set_card(5, "Loan origination system for RRBs; LOS")
    llm = ScriptedLLM({SCREEN_INSTRUCTIONS: lambda p: {"keep": [int(n) for n, t in re.findall(r"^\[(\d+)\] (.*)$", p, re.MULTILINE)
                                                                if "Loan origination system" in t]}})
    searcher, _ = build(gem, clock, bid_cache, llm, the_site(3, 5))
    found = await searcher.search(LOS, plan())
    assert "what it buys: Loan origination system for RRBs" in screened(llm)
    assert ids(found) == {5} and "vague title" not in " ".join(found.candidates[0].why)


async def test_the_window_is_applied_locally(gem, clock, bid_cache):
    old = mk(6, "Loan origination system old", published=datetime(2026, 10, 30))
    searcher, _ = build(gem, clock, bid_cache, keep_all(), {f"category|{CATEGORY}": [ROWS[1], old]})
    found = await searcher.search(LOS, plan(published_from=at(2, 0)))
    assert 6 not in ids(found) and 1 in ids(found)


async def test_hard_buyer_filter_applies_and_preferred_buyers_only_rank(gem, clock, bid_cache):
    searcher, site = build(gem, clock, bid_cache, keep_all(), the_site(1, 3, 4))
    found = await searcher.search(LOS, plan(departments=["REGIONAL RURAL BANKS"]))
    assert ids(found) == {4}
    assert "exact|REGIONAL RURAL BANKS" in {k for k, _ in site.served}
    found = await searcher.search(LOS, plan(prefer_departments=["REGIONAL RURAL BANKS"]))
    assert ids(found) == {1, 3, 4}
    assert "preferred buyer" in next(c for c in found.candidates if c.bid.b_id == 4).why


async def test_bid_types_filter_the_pool(gem, clock, bid_cache):
    ra = mk(7, "Loan origination reverse auction", bid_type=2)
    searcher, _ = build(gem, clock, bid_cache, keep_all(), {f"category|{CATEGORY}": [ROWS[1], ra]})
    assert ids(await searcher.search(LOS, plan(bid_types=[2]))) == {7}


async def test_closing_soonest_first_and_capped(gem, clock, bid_cache):
    searcher, _ = build(gem, clock, bid_cache, keep_all(), the_site(1, 3, 4, 5), cap=3)
    found = await searcher.search(LOS, plan())
    closings = [c.bid.closing for c in found.candidates]
    assert closings == sorted(closings) and len(found.candidates) == 3 and found.over_cap
    assert found.cut == 1  # how many were cut, for the run to tell the user


async def test_the_second_stage_keeps_only_the_cap_the_first_left(gem, clock, bid_cache):
    # the cap is per topic across both stages: the graph passes what is left of it into stage 2
    many = [mk(100 + i, f"Loan origination item {i}") for i in range(25)]
    searcher, _ = build(gem, clock, bid_cache, keep_all(), {"fullText|loan origination": many}, cap=12)
    p = plan(categories=[], published_from=None, published_to=None, closing_from=None)
    first = await searcher.search(LOS, p, stage="first")
    assert len(first.candidates) == 10 and first.cut == 0
    second = await searcher.search(LOS, p, stage="rest", progress=first.progress,
                                   exclude={c.bid.b_id for c in first.candidates}, cap=2)
    assert len(second.candidates) == 2 and second.cut == 13 and second.over_cap


async def test_kept_candidates_are_stored_in_the_bid_cache(gem, clock, bid_cache):
    searcher, _ = build(gem, clock, bid_cache, keep_all(), the_site(1, 4))
    found = await searcher.search(LOS, plan())
    assert found.candidates and all(bid_cache.get(c.bid.b_id) is not None for c in found.candidates)


async def test_an_empty_pool_gives_nothing_and_skips_the_screen(gem, clock, bid_cache):
    llm = ScriptedLLM({SCREEN_INSTRUCTIONS: [{"keep": []}]})
    searcher, _ = build(gem, clock, bid_cache, llm, the_site(1, 4))
    found = await searcher.search(LOS, plan(published_from=at(12), published_to=at(13)))
    assert found.candidates == [] and found.pool == 0 and llm.calls == []


async def test_a_failed_title_screen_keeps_every_title(gem, clock, bid_cache):
    llm = ScriptedLLM({SCREEN_INSTRUCTIONS: [{"keep": "not a list"}]})  # never valid, even on retry
    searcher, _ = build(gem, clock, bid_cache, llm, the_site(1, 2, 3, 4))
    found = await searcher.search(LOS, plan())
    assert ids(found) == {1, 2, 3, 4}
    assert any("screen failed" in w for c in found.candidates for w in c.why)
