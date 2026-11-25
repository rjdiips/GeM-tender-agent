from datetime import datetime, timedelta

from gem_agent.agents.chat import UNDERSTAND_INSTRUCTIONS
from gem_agent.agents.probe import probe_live
from gem_agent.agents.schemas import ProbeResult, ResolvedWindow, TopicDefinition
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


def closing_at(r: dict, when: datetime) -> dict:
    return {**r, "final_end_date_sort": [when.strftime("%Y-%m-%dT%H:%M:%SZ")]}


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
    assert p.strong == 12 and len(p.top_titles) <= 8 and ("fullText|KYC", 1) not in site.served  # 10 of AML's 12 on page 1
    assert p.meaning == AML_TOPIC.meaning


async def test_probe_titles_stay_inside_a_published_window(gem, clock):
    old = rows(500, 3, published=T0 - timedelta(days=30))
    live, _ = make(gem, clock, {"fullText|AML": [*old, *rows(100, 2)]})
    window = ResolvedWindow(published_from=T0 - timedelta(days=1), published_to=T0 + timedelta(days=1))
    p = await probe_live(live, AML_TOPIC, window)
    assert p.top_titles == ["Bid 100", "Bid 101"]


async def test_probe_counts_distinct_bids_in_window(gem, clock):
    old = rows(500, 1, published=T0 - timedelta(days=30))
    live, _ = make(gem, clock, {"fullText|AML": [*rows(100, 1), *old], "fullText|anti money laundering": rows(100, 1)})
    window = ResolvedWindow(published_from=T0 - timedelta(days=1), published_to=T0 + timedelta(days=1))
    p = await probe_live(live, AML_TOPIC, window)
    assert p.strong == 1


async def test_probe_does_not_count_all_open_bids(gem, clock):
    live, site = make(gem, clock, {"fullText|AML": rows(100, 3), "fullText|": rows(400, 30)})
    p = await probe_live(live, AML_TOPIC, ResolvedWindow())
    assert p.strong == 3 and ("fullText|", 1) not in site.served


def test_old_probe_with_window_count_loads():
    assert ProbeResult.model_validate({"meaning": "m", "window_count": 5, "strong": 2}).strong == 2


async def test_probe_counts_only_bids_published_before_a_named_end_date(gem, clock):
    old = rows(100, 1, published=T0 - timedelta(days=2))
    live, _ = make(gem, clock, {"fullText|AML": [*old, *rows(200, 2)]})
    p = await probe_live(live, AML_TOPIC, ResolvedWindow(published_to=T0 - timedelta(days=1)))
    assert p.strong == 1 and p.top_titles == ["Bid 100"]


async def test_probe_counts_only_bids_closing_inside_a_closing_window(gem, clock):
    inside = rows(100, 2)
    outside = [closing_at(r, T0 + timedelta(days=20)) for r in rows(200, 1)]
    live, _ = make(gem, clock, {"fullText|AML": [*inside, *outside]})
    window = ResolvedWindow(closing_from=T0 + timedelta(days=1), closing_to=T0 + timedelta(days=10))
    p = await probe_live(live, AML_TOPIC, window)
    assert p.strong == 2 and p.top_titles == ["Bid 100", "Bid 101"]


async def test_probe_counts_only_bids_closing_after_a_named_date(gem, clock):
    after = [closing_at(r, datetime(2026, 11, 25, 15)) for r in rows(100, 1)]
    before = [closing_at(r, datetime(2026, 11, 20, 15)) for r in rows(200, 1)]
    live, _ = make(gem, clock, {"fullText|AML": [*after, *before]})
    p = await probe_live(live, AML_TOPIC, ResolvedWindow(closing_from=datetime(2026, 11, 24)))
    assert p.strong == 1 and p.top_titles == ["Bid 100"]


def test_understand_instructions_keep_a_general_class_whole():
    assert "general class" in UNDERSTAND_INSTRUCTIONS and "never narrow" in UNDERSTAND_INSTRUCTIONS
