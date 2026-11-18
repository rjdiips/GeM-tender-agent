"""Judge with the real model on a real bid (GEM/2026/B/8162911, a bank's management-consultancy RFP).
Run with: pytest -m live    (~10 GeM requests, 4-8 model calls)"""
import pytest

from gem_agent.agents.collector import Collector
from gem_agent.agents.judge import Judge
from gem_agent.agents.models import chat_model
from gem_agent.agents.schemas import TopicDefinition
from gem_agent.cache.docs import DocCache
from gem_agent.cache.verdicts import VerdictCache
from gem_agent.tools.gem.search import CategorySearch, search_bids
from gem_agent.tools.http_client import BidplusSession, ClientPool
from tests.agents.helpers import LOS

pytestmark = pytest.mark.live

CONSULT = TopicDefinition(
    user_text="management consultancy tenders for banks",
    meaning="Hiring management consultants to advise a bank or financial institution on strategy, operations or "
            "transformation",
    domain="consulting for banking / financial sector",
    match_terms=["management consultant", "consultancy", "consulting services", "transformation", "advisory"],
    related_but_not_enough=["IT system implementation", "audit services"],
    not_this=["engineering design consultancy for construction"],
)


async def test_judge_matches_on_meaning_with_checked_quotes(tmp_path):
    pool = ClientPool.shared()
    try:
        session = BidplusSession(pool)
        found = await search_bids(session, CategorySearch(bid_number="GEM/2026/B/8162911"))
        if found.empty_confirmed:
            pytest.skip("GEM/2026/B/8162911 is no longer open")
        cache = DocCache(tmp_path)
        files = await Collector(session, cache).collect(found.bids[0])
        judge = Judge(chat_model(), cache, VerdictCache(tmp_path))

        yes = await judge.judge(CONSULT, found.bids[0], files)
        assert yes.relevant and any(e.verified for e in yes.evidence) and not yes.needs_review
        assert yes.key_facts and yes.key_facts.emd

        no = await judge.judge(LOS, found.bids[0], files)
        assert not no.relevant
    finally:
        await pool.aclose()
