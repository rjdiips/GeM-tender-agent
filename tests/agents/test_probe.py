import json
from datetime import datetime, timedelta

import httpx

from gem_agent.agents.chat import UNDERSTAND_INSTRUCTIONS
from gem_agent.agents.probe import probe_live
from gem_agent.agents.schemas import ResolvedWindow, TopicDefinition
from gem_agent.tools.gem.live import LiveSearcher
from gem_agent.tools.http_client import BidplusSession, ClientPool
from tests.index.helpers import row
from tests.tools.live_helpers import FakeSearchSite

B = "https://bidplus.gem.gov.in"
T0 = datetime(2026, 11, 18, 12, 0)

AML_TOPIC = TopicDefinition(
    user_text="AML tenders", meaning="Anti-Money Laundering software", domain="banking",
    match_terms=["AML", "anti money laundering", "transaction monitoring", "KYC"], acronyms={},
    related_but_not_enough=[], not_this=[])


def rows(prefix: int, n: int, published: datetime = T0) -> list[dict]:
    return [row(prefix + i, published, published + timedelta(days=5), f"Bid {prefix + i}") for i in range(n)]


def make(gem, clock, by_query):
    site = FakeSearchSite(by_query)
    gem.add("GET", f"{B}/advance-search", "advance_search.html.gz")
    gem.add("POST", f"{B}/all-bids-data", site)
    session = BidplusSession(ClientPool(transport=gem.transport(), clock=clock, sleep=clock.sleep))
    return LiveSearcher(session), site


async def test_probe_counts_bids_matching_the_first_three_terms(gem, clock):
    live, site = make(gem, clock, {"fullText|AML": rows(100, 12), "fullText|transaction monitoring": rows(200, 2),
                                   "fullText|KYC": rows(300, 5), "fullText|": rows(400, 30)})
    p = await probe_live(live, AML_TOPIC, ResolvedWindow())
    assert p.strong == 14 and len(p.top_titles) <= 8 and ("fullText|KYC", 1) not in site.served
    assert p.window_count == 30 and p.meaning == AML_TOPIC.meaning


async def test_probe_titles_stay_inside_a_published_window(gem, clock):
    old = rows(500, 3, published=T0 - timedelta(days=30))
    live, _ = make(gem, clock, {"fullText|AML": [*old, *rows(100, 2)]})
    window = ResolvedWindow(published_from=T0 - timedelta(days=1), published_to=T0 + timedelta(days=1))
    p = await probe_live(live, AML_TOPIC, window)
    assert p.top_titles == ["Bid 100", "Bid 101"]


async def test_probe_survives_a_failed_window_count(gem, clock):
    site = FakeSearchSite({"fullText|AML": rows(100, 3)})

    def handler(request):
        payload = json.loads(dict(httpx.QueryParams(request.content.decode()))["payload"])
        if payload["param"]["searchBid"] == "":
            return httpx.Response(500, text="boom")
        return site(request)

    gem.add("GET", f"{B}/advance-search", "advance_search.html.gz")
    gem.add("POST", f"{B}/all-bids-data", handler)
    live = LiveSearcher(BidplusSession(ClientPool(transport=gem.transport(), clock=clock, sleep=clock.sleep)))
    p = await probe_live(live, AML_TOPIC, ResolvedWindow())
    assert p.strong == 3 and p.window_count == 0


def test_understand_instructions_keep_a_general_class_whole():
    assert "general class" in UNDERSTAND_INSTRUCTIONS and "never narrow" in UNDERSTAND_INSTRUCTIONS
