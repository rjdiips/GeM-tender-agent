"""Collector against the real site, into a temporary cache. Run with: pytest -m live

About 12 requests across bidplus, fulfilment and admin hosts (shared limiter: safe beside the index jobs).
"""
import pytest

from gem_agent.agents.collector import Collector
from gem_agent.cache.docs import DocCache
from gem_agent.tools.gem.models import BidType
from gem_agent.tools.gem.search import AllBidsQuery, CategorySearch, list_all_bids, search_bids
from gem_agent.tools.http_client import BidplusSession, ClientPool

pytestmark = pytest.mark.live


async def test_collect_bid_with_attachments_then_reverse_auction(tmp_path):
    pool = ClientPool.shared()
    try:
        session = BidplusSession(pool)
        collector = Collector(session, DocCache(tmp_path))
        found = await search_bids(session, CategorySearch(bid_number="GEM/2026/B/8162911"))
        if found.empty_confirmed:
            pytest.skip("GEM/2026/B/8162911 is no longer open")
        files = await collector.collect(found.bids[0])
        statuses = {f.url.split("?")[0].rsplit("/", 2)[-2]: f.status for f in files.files}
        print(statuses)
        assert files.main_ok and files.corrigendum is not None
        assert any(f.role == "gtc" and f.status == "ok" for f in files.files)
        assert any(f.status == "placeholder" for f in files.files)
        assert any(f.role == "attachment" and f.status == "ok" and f.pages > 1 for f in files.files)

        again = await collector.collect(found.bids[0])
        assert again == files  # served from the cache

        listing = await list_all_bids(session, AllBidsQuery())
        ra = next(b for b in listing.bids if b.bid_type == BidType.RA and b.eval_type == 0 and b.parent_b_id)
        ra_files = await collector.collect(ra)
        assert ra_files.main_ok
        assert any(f.role == "parent_document" and f.status == "ok" for f in ra_files.files)
    finally:
        await pool.aclose()
