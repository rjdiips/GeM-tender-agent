import httpx
import pytest

from gem_agent.tools.http_client import (
    BidplusSession,
    ClientPool,
    GemHttpError,
    HostNotAllowed,
    is_no_data,
    is_real_error,
)

BIDPLUS = "https://bidplus.gem.gov.in"


def make_pool(gem, clock, **kw):
    return ClientPool(transport=gem.transport(), clock=clock, sleep=clock.sleep, **kw)


def test_no_data_404_is_an_empty_result_not_an_error():
    resp = httpx.Response(404, text='{"status":0,"code":404,"message":"No data found"}')
    assert is_no_data(resp)


def test_plain_404_is_not_no_data():
    assert not is_no_data(httpx.Response(404, text="<html>Not Found</html>"))


@pytest.mark.parametrize("code", [400, 401, 410, 422])
def test_client_errors_are_real_errors(code):
    assert is_real_error(httpx.Response(code, text="x"))


def test_no_data_404_is_not_an_error():
    assert not is_real_error(httpx.Response(404, text="No data found"))


async def test_pool_refuses_hosts_outside_allowlist(gem, clock):
    pool = make_pool(gem, clock)
    with pytest.raises(HostNotAllowed):
        pool.client_for("https://www.tcpdf.org/")


async def test_one_client_per_host(gem, clock):
    pool = make_pool(gem, clock)
    a = pool.client_for(f"{BIDPLUS}/all-bids")
    b = pool.client_for(f"{BIDPLUS}/advance-search")
    c = pool.client_for("https://admin.gem.gov.in/apis/v1/gtc/pdfByDate/?date=20261014")
    assert a is b and a is not c


async def test_no_data_response_is_returned_without_retry(gem, clock):
    gem.add("POST", f"{BIDPLUS}/search-bids", "search_empty.json")
    pool = make_pool(gem, clock)
    resp = await pool.request("POST", f"{BIDPLUS}/search-bids", data={"a": "b"})
    assert resp.status_code == 404 and len(gem.requests) == 1


async def test_server_error_is_retried_then_succeeds(gem, clock):
    gem.add("POST", f"{BIDPLUS}/search-bids", "search_server_error.json", "search_ministry.json")
    pool = make_pool(gem, clock)
    resp = await pool.request("POST", f"{BIDPLUS}/search-bids", data={})
    assert resp.status_code == 200 and len(gem.requests) == 2
    assert 2.0 in clock.sleeps  # first back-off step


async def test_gives_up_after_three_retries(gem, clock):
    gem.add("GET", f"{BIDPLUS}/x", lambda r: httpx.Response(503))
    pool = make_pool(gem, clock)
    with pytest.raises(GemHttpError) as err:
        await pool.request("GET", f"{BIDPLUS}/x")
    assert err.value.status == 503 and len(gem.requests) == 4


async def test_last_retry_uses_a_fresh_session(gem, clock):
    resets = []
    gem.add("GET", f"{BIDPLUS}/x", lambda r: httpx.Response(503))
    pool = make_pool(gem, clock)
    pool.client_for(f"{BIDPLUS}/x").on_reset.append(lambda: resets.append(True))
    with pytest.raises(GemHttpError):
        await pool.request("GET", f"{BIDPLUS}/x")
    assert resets == [True]


async def test_requests_to_one_host_are_spaced_by_floor(gem, clock):
    gem.add("GET", f"{BIDPLUS}/x", lambda r: httpx.Response(200, text="ok"))
    pool = make_pool(gem, clock)
    await pool.request("GET", f"{BIDPLUS}/x")
    await pool.request("GET", f"{BIDPLUS}/x")
    assert clock.sleeps == [1.0]


async def test_session_reads_token_from_page_and_posts_it(gem, clock):
    gem.add("GET", f"{BIDPLUS}/advance-search", "advance_search.html.gz")
    gem.add("POST", f"{BIDPLUS}/ministry-list-adv", "ministry_list.json")
    session = BidplusSession(make_pool(gem, clock))
    resp = await session.post_form("ministry-list-adv", {})
    assert resp.status_code == 200
    form = gem.form(1)
    assert form["csrf_bd_gem_nk"] == "c812f2e7a3c30d73da5b0c53af874602"
    assert gem.requests[1].headers["X-Requested-With"] == "XMLHttpRequest"


async def test_token_is_fetched_once_per_session(gem, clock):
    gem.add("GET", f"{BIDPLUS}/advance-search", "advance_search.html.gz")
    gem.add("POST", f"{BIDPLUS}/ministry-list-adv", "ministry_list.json")
    session = BidplusSession(make_pool(gem, clock))
    await session.post_form("ministry-list-adv", {})
    await session.post_form("ministry-list-adv", {})
    assert [r.url.path for r in gem.requests].count("/advance-search") == 1


async def test_fresh_session_refetches_token_for_last_retry(gem, clock):
    gem.add("GET", f"{BIDPLUS}/advance-search", "advance_search.html.gz")
    gem.add("POST", f"{BIDPLUS}/ministry-list-adv",
            *[lambda r: httpx.Response(500)] * 3, "ministry_list.json")
    session = BidplusSession(make_pool(gem, clock))
    resp = await session.post_form("ministry-list-adv", {})
    assert resp.status_code == 200
    assert [r.url.path for r in gem.requests].count("/advance-search") == 2


async def test_two_pools_with_a_shared_dir_space_requests_between_them(gem, clock, tmp_path):
    gem.add("GET", f"{BIDPLUS}/x", lambda r: httpx.Response(200, text="ok"))
    one = make_pool(gem, clock, shared_dir=tmp_path)
    two = make_pool(gem, clock, shared_dir=tmp_path)
    await one.request("GET", f"{BIDPLUS}/x")
    await two.request("GET", f"{BIDPLUS}/x")
    # Both requests do post-request sleeps to maintain spacing via the lock
    assert clock.sleeps == [1.0, 1.0]
    # Clean requests don't write the state file (only writes on interval/trouble change)
    assert not (tmp_path / "bidplus.gem.gov.in.json").exists()


async def test_403_starts_a_fresh_session_on_the_first_retry(gem, clock):
    resets = []
    gem.add("GET", f"{BIDPLUS}/x", lambda r: httpx.Response(403), lambda r: httpx.Response(200, text="ok"))
    pool = make_pool(gem, clock)
    pool.client_for(f"{BIDPLUS}/x").on_reset.append(lambda: resets.append(True))
    resp = await pool.request("GET", f"{BIDPLUS}/x")
    assert resp.status_code == 200 and resets == [True]


async def test_403_does_not_reset_a_session_already_renewed_by_another_request(gem, clock):
    """Bids are read concurrently: a 403 from a stale session must not wipe the session another bid just renewed."""
    resets = []
    gem.add("GET", f"{BIDPLUS}/x", lambda r: httpx.Response(403), lambda r: httpx.Response(200, text="ok"))
    pool = make_pool(gem, clock)
    host = pool.client_for(f"{BIDPLUS}/x")
    host.on_reset.append(lambda: resets.append(True))

    async def form():  # another request renews the session between building this form and sending it
        if not resets:
            await host.reset()
        return {}

    resp = await pool.request("GET", f"{BIDPLUS}/x", data=form)
    assert resp.status_code == 200 and resets == [True]


async def test_csrf_post_403_gets_a_new_token_on_the_first_retry(gem, clock):
    gem.add("GET", f"{BIDPLUS}/advance-search", "advance_search.html.gz")
    gem.add("POST", f"{BIDPLUS}/public-bid-other-details/1", lambda r: httpx.Response(403),
            lambda r: httpx.Response(200, text="{}"))
    session = BidplusSession(make_pool(gem, clock))
    resp = await session.post_form("public-bid-other-details/1", {})
    assert resp.status_code == 200
    paths = [r.url.path for r in gem.requests]
    assert paths == ["/advance-search", "/public-bid-other-details/1", "/advance-search", "/public-bid-other-details/1"]


async def test_redirect_to_host_outside_allowlist_is_refused(gem, clock):
    gem.add("GET", f"{BIDPLUS}/go", lambda r: httpx.Response(302, headers={"Location": "http://169.254.169.254/x"}))
    pool = make_pool(gem, clock)
    with pytest.raises(HostNotAllowed):
        await pool.request("GET", f"{BIDPLUS}/go")


async def test_redirect_within_allowlist_is_followed(gem, clock):
    gem.add("GET", f"{BIDPLUS}/go", lambda r: httpx.Response(302, headers={"Location": f"{BIDPLUS}/there"}))
    gem.add("GET", f"{BIDPLUS}/there", lambda r: httpx.Response(200, text="ok"))
    pool = make_pool(gem, clock)
    assert (await pool.request("GET", f"{BIDPLUS}/go")).text == "ok"
