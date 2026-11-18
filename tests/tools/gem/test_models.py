import json
from datetime import datetime

from gem_agent.tools.gem.models import Bid, BidType
from tests.conftest import fixture_bytes


def docs(name):
    return json.loads(fixture_bytes(name))["response"]["response"]["docs"]


def test_bid_parsed_from_listing_row():
    bid = Bid.from_listing(docs("search_category_p1.json")[0])
    assert bid.b_id == 8667988
    assert bid.bid_number == "GEM/2025/B/6960868"
    assert bid.title == "Hiring of Agency for IT Projects- Milestone basis"
    assert bid.category_id == "services_home_itse_hi12433188"
    assert bid.department == "Energy and Petro Chemicals Department Gujarat"
    assert bid.bid_type is BidType.BID
    assert bid.is_high_value is True


def test_missing_ministry_is_none():
    bid = Bid.from_listing(docs("search_category_p1.json")[0])
    assert bid.ministry is None


def test_site_dates_are_ist_wall_clock_despite_z_suffix():
    # The site renders final_*_date_sort with timeZone 'UTC', so "…Z" is really local (IST) clock time.
    bid = Bid.from_listing(docs("search_category_p1.json")[0])
    assert bid.closing == datetime(2026, 11, 30, 16, 0, 0)
    assert bid.closing.tzinfo is None


def test_reverse_auction_carries_its_parent_bid():
    ra = Bid.from_listing(docs("allbids_latest_p1.json")[0])
    assert ra.bid_type is BidType.RA
    assert ra.bid_number == "GEM/2026/R/770918"
    assert ra.parent_b_id == 9471263
    assert ra.parent_bid_number == "GEM/2026/B/7667009"


def test_ordinary_bid_has_no_parent():
    assert Bid.from_listing(docs("search_category_p1.json")[0]).parent_b_id is None
