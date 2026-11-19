"""Builds the real services: one shared client pool (with the cross-process rate limiter), the live searcher and bid
cache, the local models, the caches and the five agents. Used by the web app and by `gem-agent chat`."""
from __future__ import annotations

import functools
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta

from gem_agent.agents.chat import ChatAgent
from gem_agent.agents.collector import Collector
from gem_agent.agents.judge import Judge
from gem_agent.agents.models import chat_model
from gem_agent.agents.planner import Planner
from gem_agent.agents.probe import probe_live
from gem_agent.agents.rulings import RulingsAgent
from gem_agent.agents.searcher import BASE_SPECS, Searcher
from gem_agent.cache.docs import DocCache
from gem_agent.cache.lookups import LookupCatalog
from gem_agent.cache.search_cache import SearchCache
from gem_agent.cache.verdicts import VerdictCache
from gem_agent.config import Settings
from gem_agent.graph.events import EventBus
from gem_agent.graph.services import Services
from gem_agent.graph.store import RunStore
from gem_agent.index.cards import write_cards
from gem_agent.index.db import BidIndex
from gem_agent.memory.topics import TopicStore
from gem_agent.memory.user_memory import MemoryStore
from gem_agent.retrieval.embed import BgeM3Embedder, SmallEmbedder, pick_device
from gem_agent.tools.gem.live import LiveSearcher
from gem_agent.tools.gem.models import site_now
from gem_agent.tools.http_client import BidplusSession, ClientPool

log = logging.getLogger(__name__)


def _judge_embedder(settings: Settings):
    device = pick_device(settings.embed_device, min_free_mb=settings.embed_min_free_gpu_mb)
    if device == "cuda":
        return BgeM3Embedder(settings.embed_model, device=device)
    return SmallEmbedder(settings.judge_cpu_embed_model)


def build_services(settings: Settings) -> tuple[Services, DocCache, list[Callable[[], Awaitable[None]]]]:
    pool = ClientPool.shared(settings)
    session = BidplusSession(pool)
    bid_cache = BidIndex(settings.bid_cache_path)
    live = LiveSearcher(session, cache=SearchCache(settings.cache_dir / "searches"),
                        cached_keys=frozenset(s.key for s in BASE_SPECS))
    cache = DocCache(settings.cache_dir)
    model = chat_model(settings)
    catalog = LookupCatalog(settings.cache_dir, bid_cache)
    collector = Collector(session, cache)
    memory, topics = MemoryStore(settings.knowledge_dir), TopicStore(settings.knowledge_dir)
    services = Services(
        chat=ChatAgent(model, probe=functools.partial(probe_live, live), memory=memory, topics=topics),
        planner=Planner(model, catalog),
        searcher=Searcher(model, bid_cache, live),
        collector=collector,
        # Pages are ranked by meaning too: bge-m3 on the GPU; on the CPU (vLLM fills the card, so always in practice)
        # bge-m3 costs ~0.4 s a page, so the small English model ranks them instead (17 Nov). Without either, only
        # pages sharing the topic's words reached the judge. Bids are searched live, so bge-m3 is only loaded here,
        # and only when the GPU has room.
        judge=Judge(model, cache, VerdictCache(settings.cache_dir), embedder=_judge_embedder(settings)),
        store=RunStore(settings.runs_dir), bus=EventBus(), memory=memory, now=site_now, rulings=RulingsAgent(model),
        checkpoint_path=settings.runs_dir / "checkpoints.sqlite",
        cards=lambda found: write_cards(bid_cache, collector, model, bids=found),
    )
    settings.runs_dir.mkdir(parents=True, exist_ok=True)

    async def refresh_lookups() -> None:
        if not catalog.is_fresh(site_now().date()):
            log.info("refreshing GeM lookup lists")
            await catalog.refresh(session, today=site_now().date())

    async def prune_bid_cache() -> None:
        log.info("bid cache: pruned %d bids closed over 30 days ago",
                 bid_cache.prune(closed_before=site_now() - timedelta(days=30)))

    return services, cache, [refresh_lookups, prune_bid_cache]
