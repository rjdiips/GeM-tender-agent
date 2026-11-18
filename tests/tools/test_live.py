from datetime import datetime, timedelta

import httpx
import pytest

from gem_agent.cache.search_cache import SearchCache
from gem_agent.tools.gem.live import LiveSearcher, SearchProgress, SearchSpec
from gem_agent.tools.http_client import BidplusSession, ClientPool
from tests.index.helpers import row
from tests.tools.live_helpers import FakeSearchSite

B = "https://bidplus.gem.gov.in"
T0 = datetime(2026, 11, 18, 12, 0)
CATEGORY = "services_home_itse_hi12433188"


def rows(prefix: int, n: int, department: str = "Department of X") -> list[dict]:
    return [row(prefix + i, T0, T0 + timedelta(days=5), f"Bid {prefix + i}", department=department) for i in range(n)]


def make_live(gem, clock, by_query, **kwargs):
    site = FakeSearchSite(by_query)
    gem.add("GET", f"{B}/advance-search", "advance_search.html.gz")
    gem.add("POST", f"{B}/all-bids-data", site)
    gem.add("POST", f"{B}/search-bids", site)
    session = BidplusSession(ClientPool(transport=gem.transport(), clock=clock, sleep=clock.sleep))
    return LiveSearcher(session, **kwargs), site


async def test_every_page_of_a_search_is_read(gem, clock):
    live, site = make_live(gem, clock, {"fullText|aml": rows(1000, 25)})
    spec = SearchSpec(kind="fullText", value="aml")
    first = await live.first_pages([spec])
    assert first[0].progress == SearchProgress(num_found=25, pages_read=1, page_count=3)
    assert len(first[0].bids) == 10
    more = await live.rest([spec], {spec.key: first[0].progress})
    assert len(first[0].bids) + len(more[0].bids) == 25 and more[0].progress.pages_read == 3
    assert site.served == [(spec.key, 1), (spec.key, 2), (spec.key, 3)]


async def test_a_search_whose_first_page_repeats_an_earlier_one_is_read_once(gem, clock):
    shared = rows(2000, 15)
    live, site = make_live(gem, clock, {"fullText|payment gateway": shared, "fullText|payment hub": shared})
    gateway = SearchSpec(kind="fullText", value="payment gateway")
    hub = SearchSpec(kind="fullText", value="payment hub")
    a, b = await live.first_pages([gateway, hub])
    assert b.progress.same_as == gateway.key and b.bids == []
    await live.rest([gateway, hub], {gateway.key: a.progress, hub.key: b.progress})
    assert [k for k, p in site.served if p == 2] == [gateway.key]


async def test_a_fulltext_search_over_the_limit_is_skipped(gem, clock):
    big = rows(3000, 25)
    live, _ = make_live(gem, clock, {"fullText|department": big, "exact|Department of Revenue": big}, skip_over=15)
    ft, ex = await live.first_pages([SearchSpec(kind="fullText", value="department"),
                                     SearchSpec(kind="exact", value="Department of Revenue")])
    assert ft.progress.skipped and ft.bids == [] and not ex.progress.skipped
    assert len(ex.bids) == 10


async def test_no_data_found_is_an_empty_complete_search(gem, clock):
    live, _ = make_live(gem, clock, {})
    (out,) = await live.first_pages([SearchSpec(kind="fullText", value="nothing")])
    assert out.progress == SearchProgress(num_found=0, pages_read=1, page_count=0) and out.bids == []


async def test_a_search_that_fails_midway_keeps_its_pages_and_the_run_goes_on(gem, clock):
    site = FakeSearchSite({"fullText|aml": rows(4000, 25), "fullText|kyc": rows(5000, 15)})

    def flaky(request: httpx.Request) -> httpx.Response:
        payload = dict(httpx.QueryParams(request.content.decode()))["payload"]
        if '"searchBid": "aml"' in payload and '"page": 2' in payload:
            return httpx.Response(500, text="boom")
        return site(request)

    gem.add("GET", f"{B}/advance-search", "advance_search.html.gz")
    gem.add("POST", f"{B}/all-bids-data", flaky)
    session = BidplusSession(ClientPool(transport=gem.transport(), clock=clock, sleep=clock.sleep))
    live = LiveSearcher(session)
    aml, kyc = SearchSpec(kind="fullText", value="aml"), SearchSpec(kind="fullText", value="kyc")
    first = await live.first_pages([aml, kyc])
    progress = {o.spec.key: o.progress for o in first}
    more = await live.rest([aml, kyc], progress)
    assert more[0].progress.failed and more[0].progress.pages_read == 1 and len(more[1].bids) == 5
    assert len(first[1].bids) + len(more[1].bids) == 15
    assert more[1].progress.pages_read == 2 and not more[1].progress.failed


async def test_a_cached_base_search_is_reused_within_an_hour(gem, clock, tmp_path):
    key = f"category|{CATEGORY}"
    spec = SearchSpec(kind="category", value=CATEGORY)
    cache = SearchCache(tmp_path, now=lambda: T0)
    live, site = make_live(gem, clock, {key: rows(6000, 25)}, cache=cache, cached_keys=frozenset({key}))
    (first,) = await live.first_pages([spec])
    assert [p for _, p in site.served] == [1]
    assert cache.get(key) is None  # stage 1 never stores a search that has more pages
    (more,) = await live.rest([spec], {key: first.progress})
    assert [p for _, p in site.served] == [1, 2, 3] and more.progress.pages_read == 3
    assert cache.get(key)[0] == 25 and len(cache.get(key)[1]) == 25

    again, site2 = make_live(gem, clock, {key: rows(6000, 25)}, cache=cache, cached_keys=frozenset({key}))
    (outcome,) = await again.first_pages([spec])
    assert len(outcome.bids) == 25
    assert outcome.progress.pages_read == outcome.progress.page_count and site2.served == []
    assert not outcome.progress.pending


async def test_an_old_or_unreadable_cache_file_is_ignored(tmp_path):
    docs = rows(7000, 3)
    cache = SearchCache(tmp_path, now=lambda: T0)
    cache.put("k", 3, docs)
    assert cache.get("k") == (3, docs)
    assert SearchCache(tmp_path, now=lambda: T0 + timedelta(minutes=61)).get("k") is None
    (next(tmp_path.glob("*.json"))).write_text("{not json")
    assert SearchCache(tmp_path, now=lambda: T0).get("k") is None


def _bad_body_site(gem, clock, bad_pages):
    site = FakeSearchSite({"fullText|aml": rows(8000, 25), "fullText|kyc": rows(9000, 15)})

    def handler(request: httpx.Request) -> httpx.Response:
        payload = dict(httpx.QueryParams(request.content.decode()))["payload"]
        for term, page in bad_pages:
            if f'"searchBid": "{term}"' in payload and f'"page": {page}' in payload:
                return httpx.Response(200, text="<html>captcha</html>")
        return site(request)

    gem.add("GET", f"{B}/advance-search", "advance_search.html.gz")
    gem.add("POST", f"{B}/all-bids-data", handler)
    session = BidplusSession(ClientPool(transport=gem.transport(), clock=clock, sleep=clock.sleep))
    return LiveSearcher(session)


async def test_a_page_that_is_not_json_fails_only_its_search(gem, clock):
    live = _bad_body_site(gem, clock, [("aml", 2)])
    aml, kyc = SearchSpec(kind="fullText", value="aml"), SearchSpec(kind="fullText", value="kyc")
    first = await live.first_pages([aml, kyc])
    more = await live.rest([aml, kyc], {o.spec.key: o.progress for o in first})
    assert more[0].progress.failed and more[0].progress.pages_read == 1
    assert more[1].progress.pages_read == 2 and not more[1].progress.failed


async def test_a_first_page_that_is_not_json_marks_the_search_failed(gem, clock):
    live = _bad_body_site(gem, clock, [("aml", 1)])
    aml, kyc = SearchSpec(kind="fullText", value="aml"), SearchSpec(kind="fullText", value="kyc")
    a, k = await live.first_pages([aml, kyc])
    assert a.progress.failed and a.progress.pages_read == 0 and a.bids == []
    assert not k.progress.failed and len(k.bids) == 10


async def test_a_host_outside_the_allowlist_is_not_swallowed(gem, clock, monkeypatch):
    from gem_agent.tools.http_client import HostNotAllowed

    live, _ = make_live(gem, clock, {})

    async def refuse(*args, **kwargs):
        raise HostNotAllowed("https://elsewhere.example/")

    monkeypatch.setattr("gem_agent.tools.gem.live.list_all_bids", refuse)
    with pytest.raises(HostNotAllowed):
        await live.first_pages([SearchSpec(kind="fullText", value="aml")])


async def test_a_listing_row_without_a_bid_id_fails_only_its_search(gem, clock):
    broken = rows(10000, 10)
    del broken[3]["b_id"]
    live, _ = make_live(gem, clock, {"fullText|aml": broken, "fullText|kyc": rows(11000, 10)})
    aml, kyc = SearchSpec(kind="fullText", value="aml"), SearchSpec(kind="fullText", value="kyc")
    a, k = await live.first_pages([aml, kyc])
    assert a.progress.failed and a.bids == []
    assert not k.progress.failed and len(k.bids) == 10
