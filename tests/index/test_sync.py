from datetime import datetime, timedelta

import pytest

from gem_agent.index.db import BidIndex
from gem_agent.index.sync import NeedFullLoad, embed_pending, full_load, sync_recent
from gem_agent.tools.http_client import BidplusSession, ClientPool
from tests.index.helpers import FakeListing, HashEmbedder, row

B = "https://bidplus.gem.gov.in"


def at(day, hour=12, minute=0):
    return datetime(2026, 11, day, hour, minute)


class Now:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def index():
    idx = BidIndex(":memory:")
    yield idx
    idx.close()


def setup_site(gem, clock, rows):
    listing = FakeListing(rows)
    gem.add("GET", f"{B}/advance-search", "advance_search.html.gz")
    gem.add("POST", f"{B}/all-bids-data", listing)
    return BidplusSession(ClientPool(transport=gem.transport(), clock=clock, sleep=clock.sleep)), listing


def hourly_rows(n, start=None, first_id=1):
    """n bids published an hour apart, newest last."""
    start = start or at(16, 10)
    return [row(first_id + i, start - timedelta(hours=n - i), at(30)) for i in range(n)]


async def test_full_load_stores_every_page(gem, clock, index):
    session, listing = setup_site(gem, clock, hourly_rows(25))
    report = await full_load(index, session, now=Now(at(16, 12)))
    assert report.complete and report.pages == 3 and report.new == 25
    assert listing.pages_served == [1, 2, 3]
    assert index.stats()["open"] == 25


async def test_full_load_resumes_from_checkpoint(gem, clock, index):
    session, listing = setup_site(gem, clock, hourly_rows(25))
    first = await full_load(index, session, now=Now(at(16, 12)), max_pages=2)
    assert not first.complete
    second = await full_load(index, session, now=Now(at(16, 13)))
    assert second.complete
    assert listing.pages_served == [1, 2, 3]


async def test_full_refresh_marks_missing_bids(gem, clock, index):
    rows = hourly_rows(5)
    session, listing = setup_site(gem, clock, rows)
    await full_load(index, session, now=Now(at(16, 12)))
    listing.rows = rows[1:]
    report = await full_load(index, session, now=Now(at(17, 1)))
    assert report.missed == 1 and report.gone == 0
    report = await full_load(index, session, now=Now(at(18, 1)))
    assert report.gone == 1 and index.bid_state(1) == "gone"


async def test_full_load_closes_expired_bids(gem, clock, index):
    rows = [row(1, at(10), at(16, 9)), row(2, at(10), at(30))]
    session, _ = setup_site(gem, clock, rows)
    await full_load(index, session, now=Now(at(16, 12)))
    assert index.bid_state(1) == "closed" and index.bid_state(2) == "open"


async def test_sync_needs_a_full_load_first(gem, clock, index):
    session, _ = setup_site(gem, clock, hourly_rows(5))
    with pytest.raises(NeedFullLoad):
        await sync_recent(index, session, now=Now(at(16, 12)))


async def test_sync_stops_at_first_known_page_older_than_last_sync(gem, clock, index):
    rows = hourly_rows(60)  # one per hour up to 16 Nov 10:00
    session, listing = setup_site(gem, clock, rows)
    await full_load(index, session, now=Now(at(16, 10, 30)))
    listing.pages_served.clear()
    # 12 new bids published since, one every 5 minutes
    listing.rows = rows + [row(100 + i, at(16, 11, 5 * i), at(30)) for i in range(12)]
    report = await sync_recent(index, session, now=Now(at(16, 12)))
    assert report.new == 12
    # page 1-2 hold the new bids; page 3 is all known and older than 10:00 (last sync 10:30 - 30 min margin)
    assert listing.pages_served == [1, 2, 3]


async def test_sync_pages_past_known_future_dated_auctions(gem, clock, index):
    rows = hourly_rows(30)
    future_ras = [row(500 + i, at(17, 13), at(30), bid_type=2) for i in range(10)]  # start tomorrow
    session, listing = setup_site(gem, clock, rows + future_ras)
    await full_load(index, session, now=Now(at(16, 10, 30)))
    listing.pages_served.clear()
    listing.rows = rows + future_ras + [row(900, at(16, 11), at(30))]
    report = await sync_recent(index, session, now=Now(at(16, 12)))
    assert report.new == 1
    assert listing.pages_served[:2] == [1, 2]  # page 1 is all known RAs, but not older than the last sync


async def test_sync_reads_listing_to_the_end_if_needed(gem, clock, index):
    rows = hourly_rows(5)
    session, listing = setup_site(gem, clock, rows)
    await full_load(index, session, now=Now(at(16, 10, 30)))
    listing.rows = rows + [row(100, at(16, 11), at(30))]
    report = await sync_recent(index, session, now=Now(at(16, 12)))
    assert report.new == 1 and report.pages == 1


def test_embed_pending_embeds_in_batches(index):
    from gem_agent.tools.gem.models import Bid

    index.upsert([Bid.from_listing(r) for r in hourly_rows(5)], seen_at=at(16))
    embedder = HashEmbedder()
    assert embed_pending(index, embedder, batch_size=2) == 5
    assert embedder.calls == [2, 2, 1]
    assert embed_pending(index, embedder) == 0


def test_second_sync_is_refused_while_first_holds_the_lock(tmp_path):
    from gem_agent.index.sync import SyncBusy, sync_lock

    with sync_lock(tmp_path / "sync.lock"), pytest.raises(SyncBusy), sync_lock(tmp_path / "sync.lock"):
        pass
    with sync_lock(tmp_path / "sync.lock"):
        pass


async def test_sync_stops_even_when_older_pages_hold_bids_the_full_load_skipped(gem, clock, index):
    # 16 Nov: the full load skipped ~2.7% of bids (the list shifts while it is read), so almost every older page
    # holds one unknown bid; "stop at an all-known page" then never stops and the sync crawls the whole list.
    rows = hourly_rows(80)
    session, listing = setup_site(gem, clock, rows)
    skipped = rows[5::10]  # one bid on every older page was never seen
    listing.rows = [r for r in rows if r not in skipped]
    await full_load(index, session, now=Now(at(16, 10, 30)))
    listing.rows = rows + [row(900 + i, at(16, 11, 5 * i), at(30)) for i in range(12)]
    listing.pages_served.clear()
    report = await sync_recent(index, session, now=Now(at(16, 12)))
    assert report.new >= 12
    assert len(listing.pages_served) <= 3
