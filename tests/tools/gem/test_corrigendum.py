from datetime import datetime

import pytest

from gem_agent.tools.gem.corrigendum import other_details, parse_corrigendum, view_corrigendum
from gem_agent.tools.http_client import BidplusSession, ClientPool
from tests.conftest import fixture_bytes

B = "https://bidplus.gem.gov.in"


@pytest.fixture
def session(gem, clock):
    gem.add("GET", f"{B}/advance-search", "advance_search.html.gz")
    return BidplusSession(ClientPool(transport=gem.transport(), clock=clock, sleep=clock.sleep))


async def test_corrigendum_flag(gem, session):
    gem.add("POST", f"{B}/public-bid-other-details/10037444", "other_details_corr.json")
    details = await other_details(session, 10037444)
    assert details.corrigendum is True and details.representation is False


async def test_no_corrigendum_flag(gem, session):
    gem.add("POST", f"{B}/public-bid-other-details/10170908", "other_details_ra.json")
    assert (await other_details(session, 10170908)).corrigendum is False


async def test_view_corrigendum_body_zero_means_none(gem, session):
    gem.add("POST", f"{B}/bidding/bid/viewCorrigendum/10170908", "corrigendum_none.html")
    assert await view_corrigendum(session, 10170908) is None


async def test_view_corrigendum_parses_changes(gem, session):
    gem.add("POST", f"{B}/bidding/bid/viewCorrigendum/10037444", "corrigendum.html")
    corr = await view_corrigendum(session, 10037444)
    assert corr.b_id == 10037444
    assert len(corr.entries) == 1
    entry = corr.entries[0]
    assert entry.modified_on == datetime(2026, 11, 9, 16, 48, 47)
    assert entry.changes == [("Bid extended to", "2026-11-23 16:00:00"), ("Bid Opening Date", "2026-11-24 16:00:00")]
    assert corr.extended_to == datetime(2026, 11, 23, 16, 0, 0)


def test_several_modifications_latest_extension_wins():
    one = fixture_bytes("corrigendum.html").decode()
    two = one.replace("2026-11-09 16:48:47", "2026-11-12 10:00:00").replace("2026-11-23 16:00:00", "2026-11-28 16:00:00")
    corr = parse_corrigendum(1, one.split("<script")[0] + two)
    assert len(corr.entries) == 2
    assert corr.extended_to == datetime(2026, 11, 28, 16, 0, 0)
