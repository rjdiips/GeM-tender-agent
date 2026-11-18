import json
from datetime import date

import pytest

from gem_agent.tools.gem.search import (
    AllBidsQuery,
    BoqSearch,
    CategorySearch,
    ConsigneeSearch,
    MinistrySearch,
    PageCountMismatch,
    list_all_bids,
    search_bids,
    search_bids_all,
)
from gem_agent.tools.http_client import BidplusSession, ClientPool

B = "https://bidplus.gem.gov.in"


@pytest.fixture
def session(gem, clock):
    gem.add("GET", f"{B}/advance-search", "advance_search.html.gz")
    return BidplusSession(ClientPool(transport=gem.transport(), clock=clock, sleep=clock.sleep))


def sent_payload(gem, i=-1):
    return json.loads(gem.form(i if i >= 0 else len(gem.requests) - 1)["payload"])


def test_category_payload_matches_site_form():
    q = CategorySearch(category="services_home_itse_hi12433188", end_from=date(2026, 11, 17), end_to=date(2026, 11, 20))
    assert q.payload(page=2) == {
        "searchType": "bidNumber", "bidNumber": "", "category": "services_home_itse_hi12433188",
        "bidEndFrom": "2026-11-17", "bidEndTo": "2026-11-20", "page": 2,
    }


def test_ministry_payload_matches_site_form():
    assert MinistrySearch(ministry="Ministry of Finance").payload(page=1) == {
        "searchType": "ministry-search", "ministry": "Ministry of Finance", "buyerState": "", "organization": "",
        "department": "", "bidEndFromMin": "", "bidEndToMin": "", "page": 1,
    }


def test_consignee_and_boq_payloads_match_site_form():
    assert ConsigneeSearch(state="ASSAM", city="GUWAHATI").payload(page=1) == {
        "searchType": "con", "state_name_con": "ASSAM", "city_name_con": "GUWAHATI",
        "bidEndFromCon": "", "bidEndToCon": "", "page": 1,
    }
    assert BoqSearch(boq_title="Pathology Item").payload(page=1) == {
        "searchType": "boq", "boqtitle_con": "Pathology Item", "bidvalue": "",
        "bidEndFromBoq": "", "bidEndToBoq": "", "page": 1,
    }


async def test_search_returns_page_of_bids(gem, session):
    gem.add("POST", f"{B}/search-bids", "search_category_p1.json")
    page = await search_bids(session, CategorySearch(category="services_home_itse_hi12433188"))
    assert page.num_found == 166 and page.start == 0 and len(page.bids) == 10
    assert page.page_count == 17
    assert not page.empty_confirmed
    assert sent_payload(gem)["category"] == "services_home_itse_hi12433188"


async def test_no_data_found_is_a_confirmed_empty_page(gem, session):
    gem.add("POST", f"{B}/search-bids", "search_empty.json")
    page = await search_bids(session, CategorySearch(category="home_info_soft_secu_cy58162858"))
    assert page.bids == [] and page.num_found == 0 and page.empty_confirmed


async def test_search_all_pages_until_num_found(gem, session):
    gem.add("POST", f"{B}/search-bids", "search_category_p1.json", "search_category_p2.json")
    result = await search_bids_all(session, CategorySearch(category="x"), max_pages=2)
    assert len(result.bids) == 20 and result.num_found == 166
    assert [sent_payload(gem, i)["page"] for i in (1, 2)] == [1, 2]
    assert not result.complete


async def test_search_all_drops_duplicate_bids_across_pages(gem, session):
    gem.add("POST", f"{B}/search-bids", "search_category_p1.json")
    result = await search_bids_all(session, CategorySearch(category="x"), max_pages=2)
    assert len(result.bids) == 10


async def test_search_all_flags_short_result(gem, session):
    # numFound says 1 bid, but the page carries none: the count does not match the page arithmetic.
    def short(request):
        import httpx
        body = {"status": 1, "code": 200, "response": {"response": {"numFound": 1, "start": 0, "docs": []}}}
        return httpx.Response(200, json=body)

    gem.add("POST", f"{B}/search-bids", short)
    with pytest.raises(PageCountMismatch):
        await search_bids_all(session, CategorySearch(category="x"))


async def test_all_bids_newest_first_payload(gem, session):
    gem.add("POST", f"{B}/all-bids-data", "allbids_latest_p1.json")
    page = await list_all_bids(session, AllBidsQuery(), page=1)
    assert page.num_found == 43173 and len(page.bids) == 10
    sent = sent_payload(gem)
    assert sent == {
        "page": 1,
        "param": {"searchBid": "", "searchType": "fullText"},
        "filter": {"bidStatusType": "ongoing_bids", "byType": "all", "highBidValue": "",
                   "byEndDate": {"from": "", "to": ""}, "sort": "Bid-Start-Date-Latest"},
    }
    assert gem.requests[-1].headers["Referer"] == f"{B}/all-bids"
