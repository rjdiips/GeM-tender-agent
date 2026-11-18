"""Checks the GeM tools against the real site (spec 3, build step 1). Run with: pytest -m live

About 30 requests, at least 1 s apart per host.
"""
import pytest
import pytest_asyncio

from gem_agent.tools.gem.corrigendum import other_details, view_corrigendum
from gem_agent.tools.gem.documents import attachment_links, download, fetch_bid_document
from gem_agent.tools.gem.lookups import list_categories, list_ministries, list_organizations
from gem_agent.tools.gem.models import BidType
from gem_agent.tools.gem.search import (
    AllBidsQuery,
    CategorySearch,
    MinistrySearch,
    list_all_bids,
    search_bids,
    search_bids_all,
)
from gem_agent.tools.http_client import BidplusSession, ClientPool

pytestmark = [pytest.mark.live, pytest.mark.asyncio(loop_scope="module")]
IT_MILESTONE = "services_home_itse_hi12433188"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def session():
    pool = ClientPool.shared()
    yield BidplusSession(pool)
    await pool.aclose()


async def test_lookups(session):
    ministries = await list_ministries(session)
    assert "Ministry of Finance" in ministries and "FINANCE" in ministries
    assert len(await list_organizations(session, ministry="Ministry of Finance")) > 50
    cats = {c.value: c.label for c in await list_categories(session)}
    assert len(cats) > 10000 and IT_MILESTONE in cats


async def test_empty_ministry_is_confirmed_empty(session):
    page = await search_bids(session, MinistrySearch(ministry="FINANCE"))
    assert page.empty_confirmed


async def test_category_search_covers_every_page(session):
    result = await search_bids_all(session, CategorySearch(category=IT_MILESTONE))
    assert result.complete and result.num_found > 50
    assert len(result.bids) >= result.num_found - 2  # the listing may shift while paging
    assert all(b.category_id == IT_MILESTONE for b in result.bids)


async def test_all_bids_newest_first(session):
    page = await list_all_bids(session, AllBidsQuery(sort="Bid-Start-Date-Latest"))
    published = [b.published for b in page.bids]
    assert page.num_found > 20000 and published == sorted(published, reverse=True)


async def test_documents_by_bid_type(session):
    page = await list_all_bids(session, AllBidsQuery())
    ra = next(b for b in page.bids if b.bid_type == BidType.RA and b.eval_type == 0)
    assert (await fetch_bid_document(session, ra)).kind == "pdf"
    assert (await download(session.pool, session.url(f"/showbidDocument/{ra.b_id}"))).kind == "empty"

    found = await search_bids(session, CategorySearch(category=IT_MILESTONE))
    bid = next(b for b in found.bids if b.bid_type == BidType.BID)
    doc = await fetch_bid_document(session, bid)
    assert doc.kind == "pdf"
    links = attachment_links(doc.content, allowed=session.pool.is_allowed)
    assert any(link.kind == "gtc" for link in links)


async def test_corrigendum(session):
    found = await search_bids(session, CategorySearch(bid_number="GEM/2026/B/8162911"))
    if found.empty_confirmed:
        pytest.skip("GEM/2026/B/8162911 is no longer open")
    b_id = found.bids[0].b_id
    assert (await other_details(session, b_id)).corrigendum
    corr = await view_corrigendum(session, b_id)
    assert corr is not None and corr.extended_to is not None
