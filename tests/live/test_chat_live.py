"""Chat + Planner with the real model and the real local index. Run with: pytest -m live
One GeM request (the advance-search page, for the category list); ~8 model calls."""
import functools
from datetime import timedelta

import pytest

from gem_agent.agents.chat import ChatAgent
from gem_agent.agents.models import chat_model
from gem_agent.agents.planner import Planner
from gem_agent.cache.lookups import LookupCatalog
from gem_agent.config import get_settings
from gem_agent.index.db import BidIndex
from gem_agent.index.probe import probe_site
from gem_agent.memory.topics import TopicStore
from gem_agent.memory.user_memory import MemoryStore
from gem_agent.retrieval.embed import BgeM3Embedder
from gem_agent.tools.gem.models import site_now
from gem_agent.tools.http_client import BidplusSession, ClientPool

pytestmark = pytest.mark.live


async def test_understanding_and_plans(tmp_path):
    index = BidIndex(get_settings().index_path)
    pool = ClientPool.shared()
    try:
        catalog = LookupCatalog(tmp_path, index)
        await catalog.refresh(BidplusSession(pool), today=site_now().date())
        memory = MemoryStore(tmp_path)
        chat = ChatAgent(chat_model(), probe=functools.partial(probe_site, index, BgeM3Embedder(device="cpu")),
                         memory=memory, topics=TopicStore(tmp_path))
        planner = Planner(chat_model(), catalog)
        today = site_now().date()

        los = await chat.understand("t", "LOS tenders published in the last 2 weeks", [], run_id="r1")
        assert "Loan Origination" in los.request.topics[0].meaning
        assert los.request.window.published_from.date() == today - timedelta(days=14)
        assert los.progress_line.startswith("Looking for Loan Origination System")
        plan = await planner.plan(los.request, los.request.topics[0])
        assert plan.ministries == [] and plan.departments == [] and "LOS" in plan.terms

        aml = await chat.understand("t", "AML tenders closing in the next 10 days", [], run_id="r2")
        assert aml.request.window.closing_to.date() == today + timedelta(days=10)
        assert "Money Laundering" in aml.request.topics[0].meaning

        cbs = await chat.understand("t", "we're a banking software company. show me CBS tenders from cooperative banks",
                                    [], run_id="r3")
        assert "Core Banking" in cbs.request.topics[0].meaning and not cbs.request.buyer_only
        assert any("banking software" in f.fact.lower() for f in memory.prompt_facts("t"))
        plan = await planner.plan(cbs.request, cbs.request.topics[0])
        assert plan.ministries == [] and plan.departments == []
    finally:
        await pool.aclose()
        index.close()
