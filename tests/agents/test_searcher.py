from datetime import datetime

import pytest

from gem_agent.agents.schemas import SearchPlan
from gem_agent.agents.searcher import SCREEN_INSTRUCTIONS, Searcher, SearchSettings
from gem_agent.index.db import BidIndex
from gem_agent.index.sync import embed_pending
from gem_agent.tools.gem.models import Bid
from tests.agents.helpers import LOS, ScriptedLLM
from tests.index.helpers import HashEmbedder, row


def at(day, hour=12):
    return datetime(2026, 11, day, hour)


TITLES = {
    1: ("Supply of Loan Origination System for RRBs", "Department of Financial Services", at(20)),
    2: ("LOST property lockers", "Department of Revenue", at(19)),
    3: ("Office chairs", "Department of Revenue", at(18)),
    4: ("Digital lending platform with loan origination workflow", "REGIONAL RURAL BANKS", at(25)),
    5: ("Hiring of Agency for IT Projects- Milestone basis", "Department of Financial Services", at(17)),
}


@pytest.fixture
def index():
    idx = BidIndex(":memory:")
    idx.upsert([Bid.from_listing(row(i, at(10), c, t, department=d)) for i, (t, d, c) in TITLES.items()], seen_at=at(15))
    embed_pending(idx, HashEmbedder())
    yield idx
    idx.close()


def plan(**kw):
    base = {"topic_key": LOS.key(), "text": LOS.meaning, "terms": ["Loan Origination System", "LOS", "loan origination"],
            "published_from": at(2, 0), "published_to": at(16), "closing_from": at(16)}
    return SearchPlan(**(base | kw))


def keep(*ids_by_position):
    return {SCREEN_INSTRUCTIONS: [{"keep": list(ids_by_position)}]}


def screened(llm):
    return llm.calls[0][1] if llm.calls else ""


async def test_word_and_meaning_matches_go_to_the_title_screen(index):
    def screen(prompt):
        import re
        return {"keep": [int(n) for n, title in re.findall(r"^\[(\d+)\] (.*)$", prompt, re.MULTILINE)
                         if "rigination" in title or "lending" in title]}

    llm = ScriptedLLM({SCREEN_INSTRUCTIONS: screen})
    found = await Searcher(llm.model(), index, HashEmbedder()).search(LOS, plan())
    assert {c.bid.b_id for c in found.candidates} >= {1, 4}
    assert 3 not in {c.bid.b_id for c in found.candidates}
    assert "Supply of Loan Origination System for RRBs" in screened(llm)


async def test_title_screen_never_drops_a_vague_title(index):
    llm = ScriptedLLM({SCREEN_INSTRUCTIONS: [{"keep": []}]})
    found = await Searcher(llm.model(), index, HashEmbedder(), settings=SearchSettings(semantic_pool=10)).search(LOS, plan())
    assert [c.bid.b_id for c in found.candidates] == [5]
    assert "vague title" in found.candidates[0].why[0]


async def test_closing_soonest_first_and_capped(index):
    llm = ScriptedLLM({SCREEN_INSTRUCTIONS: lambda p: {"keep": list(range(10))}})
    found = await Searcher(llm.model(), index, HashEmbedder(), settings=SearchSettings(cap=3, semantic_pool=10)).search(LOS, plan())
    closings = [c.bid.closing for c in found.candidates]
    assert closings == sorted(closings) and len(found.candidates) == 3 and found.over_cap


async def test_preferred_buyers_are_marked_and_ranked_first_within_a_day(index):
    llm = ScriptedLLM({SCREEN_INSTRUCTIONS: lambda p: {"keep": list(range(10))}})
    found = await Searcher(llm.model(), index, HashEmbedder(), settings=SearchSettings(semantic_pool=10)).search(
        LOS, plan(prefer_departments=["REGIONAL RURAL BANKS"]))
    pref = next(c for c in found.candidates if c.bid.b_id == 4)
    assert "preferred buyer" in pref.why


async def test_hard_buyer_filter_applies(index):
    llm = ScriptedLLM({SCREEN_INSTRUCTIONS: lambda p: {"keep": list(range(10))}})
    found = await Searcher(llm.model(), index, HashEmbedder()).search(LOS, plan(departments=["REGIONAL RURAL BANKS"]))
    assert {c.bid.b_id for c in found.candidates} == {4}


async def test_closed_window_gives_nothing(index):
    llm = ScriptedLLM({SCREEN_INSTRUCTIONS: [{"keep": []}]})
    found = await Searcher(llm.model(), index, HashEmbedder()).search(LOS, plan(published_from=at(12), published_to=at(13)))
    assert found.candidates == [] and found.pool == 0 and llm.calls == []


async def test_live_category_search_adds_bids_to_the_pool(gem, clock, index):
    from gem_agent.tools.http_client import BidplusSession, ClientPool

    gem.add("GET", "https://bidplus.gem.gov.in/advance-search", "advance_search.html.gz")
    gem.add("POST", "https://bidplus.gem.gov.in/search-bids", "search_category_p1.json")
    session = BidplusSession(ClientPool(transport=gem.transport(), clock=clock, sleep=clock.sleep))
    llm = ScriptedLLM({SCREEN_INSTRUCTIONS: lambda p: {"keep": list(range(40))}})
    found = await Searcher(llm.model(), index, HashEmbedder(), session=session,
                           settings=SearchSettings(category_pages=1)).search(
        LOS, plan(published_from=None, published_to=None, closing_from=None, categories=["services_home_itse_hi12433188"]))
    from_category = [c for c in found.candidates if "category search" in c.why]
    assert len(from_category) == 10 and index.get(from_category[0].bid.b_id) is not None


async def test_a_failed_screen_call_keeps_its_titles(index):
    llm = ScriptedLLM({SCREEN_INSTRUCTIONS: [{"keep": "not a list"}]})  # never valid, even on retry
    found = await Searcher(llm.model(), index, HashEmbedder(), settings=SearchSettings(semantic_pool=10)).search(LOS, plan())
    assert {c.bid.b_id for c in found.candidates} == {1, 2, 3, 4, 5}
    assert any("screen failed" in w for c in found.candidates for w in c.why)


async def test_category_search_sends_no_half_date_filter(gem, clock, index):
    # 16 Nov: closing_from set and closing_to not → only bidEndFrom sent → GeM answers 422
    from gem_agent.tools.http_client import BidplusSession, ClientPool

    gem.add("GET", "https://bidplus.gem.gov.in/advance-search", "advance_search.html.gz")
    gem.add("POST", "https://bidplus.gem.gov.in/search-bids", "search_category_p1.json")
    session = BidplusSession(ClientPool(transport=gem.transport(), clock=clock, sleep=clock.sleep))
    llm = ScriptedLLM({SCREEN_INSTRUCTIONS: lambda p: {"keep": list(range(40))}})
    await Searcher(llm.model(), index, HashEmbedder(), session=session,
                   settings=SearchSettings(category_pages=1)).search(
        LOS, plan(published_from=None, published_to=None, closing_from=at(16), categories=["services_home_itse_hi12433188"]))
    import json
    payload = json.loads(gem.form(-1)["payload"])
    assert (payload["bidEndFrom"], payload["bidEndTo"]) == ("", "")


async def test_a_failing_category_search_is_skipped(index):
    import httpx

    from gem_agent.tools.http_client import ClientPool

    class Rejecting:
        def __init__(self):
            self.pool = ClientPool(transport=httpx.MockTransport(lambda r: httpx.Response(422)), clock=at)
            self._pool = self.pool

        async def post_form(self, path, data, referer=None):
            return await self.pool.request("POST", "https://bidplus.gem.gov.in/" + path, data=data)

        async def get(self, path):
            return await self.pool.request("GET", "https://bidplus.gem.gov.in/" + path)

    llm = ScriptedLLM({SCREEN_INSTRUCTIONS: lambda p: {"keep": list(range(40))}})
    found = await Searcher(llm.model(), index, HashEmbedder(), session=Rejecting()).search(
        LOS, plan(published_from=None, published_to=None, closing_from=None, categories=["services_home_itse_hi12433188"]))
    assert {c.bid.b_id for c in found.candidates} == {1, 2, 3, 4, 5}


async def test_a_carded_vague_bid_is_screened_on_its_card(index):
    index.set_card(5, "MSME Digital Lending Suite for the bank; loan origination; digital lending")
    seen = []

    def screen(prompt):
        import re
        seen.append(prompt)
        return {"keep": [int(n) for n, t in re.findall(r"^\[(\d+)\] (.*)$", prompt, re.MULTILINE) if "Lending Suite" in t]}

    llm = ScriptedLLM({SCREEN_INSTRUCTIONS: screen})
    found = await Searcher(llm.model(), index, HashEmbedder(), settings=SearchSettings(semantic_pool=10)).search(LOS, plan())
    assert [c.bid.b_id for c in found.candidates] == [5]
    assert "what it buys: MSME Digital Lending Suite" in seen[0]
    assert "vague title" not in " ".join(found.candidates[0].why)


async def test_uncarded_vague_bids_join_every_pool(index):
    # a vague-title bid whose documents never named the subject (card '') must stay reachable for every topic
    from datetime import datetime as dt

    index.upsert([Bid.from_listing(row(9, at(11), at(21), "Custom Bid for Services - ----", department="Department of Revenue"))],
                 seen_at=at(15))
    index.set_card(9, "")
    llm = ScriptedLLM({SCREEN_INSTRUCTIONS: [{"keep": []}]})
    found = await Searcher(llm.model(), index, HashEmbedder(), settings=SearchSettings(semantic_pool=1)).search(LOS, plan())
    ids = {c.bid.b_id for c in found.candidates}
    assert 9 in ids
    assert any("no subject found" in w for c in found.candidates if c.bid.b_id == 9 for w in c.why)
    assert dt  # keeps the import used
