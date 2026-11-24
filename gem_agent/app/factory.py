"""Builds the real services: one shared client pool (with the cross-process rate limiter), the live searcher and bid
cache, the local models, the caches and the five agents. Used by the web app and by `gem-agent chat`."""
from __future__ import annotations

import functools
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path

from gem_agent.agents.chat import ChatAgent
from gem_agent.agents.collector import Collector
from gem_agent.agents.judge import Judge, verdict_version
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
from gem_agent.retrieval.embed import RemoteEmbedder, SmallEmbedder
from gem_agent.retrieval.rerank import RemoteReranker
from gem_agent.tools.gem.live import LiveSearcher
from gem_agent.tools.gem.models import site_now
from gem_agent.tools.http_client import BidplusSession, ClientPool
from gem_agent.tools.pdf.ocr import configure_tesseract

log = logging.getLogger(__name__)


def judge_embedder(settings: Settings):
    """The embedder the Judge ranks pages with: remote when a URL is set, else all-MiniLM-L6-v2.

    MiniLM is the measured default: the same top-8 hit rate as
    bge-small and bge-m3, a better top-24 and MRR, and about twice the speed. The GPU is
    left to vLLM — picking bge-m3 whenever the card had room was never measured and would
    contend with the served model.
    """
    if settings.embed_base_url:
        return RemoteEmbedder(settings.embed_base_url, settings.embed_model, api_key=settings.embed_api_key,
                              timeout_s=settings.embed_timeout_s, batch_size=settings.embed_batch_size)
    return SmallEmbedder(settings.judge_cpu_embed_model)


def judge_reranker(settings: Settings):
    """The reranker: remote only today. None keeps the fused (word + dense) order."""
    if not settings.rerank_base_url:
        return None
    return RemoteReranker(settings.rerank_base_url, settings.rerank_model, style=settings.rerank_style,
                          api_key=settings.rerank_api_key, timeout_s=settings.rerank_timeout_s)


def labelled_b_ids(path: Path, *, strict: bool = False) -> set[int]:
    """The labelled set's b_ids, by a plain JSON read: validating the whole set here would fail on label fields this
    code does not know yet, and the prune only needs the ids. No file (or an unreadable one): none.
    strict: an existing file that cannot be read or parsed raises instead, for callers that must never act without it."""
    if not Path(path).exists():
        return set()
    try:
        return {int(item["b_id"]) for item in json.loads(Path(path).read_text(encoding="utf-8"))["items"]}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        if strict:
            raise
        log.warning("labelled set %s unreadable, pruning without it: %s", path, exc)
        return set()


def startup_prune(bid_cache: BidIndex, labelled: Path, now: datetime) -> int:
    """Bids closed over 30 days ago, except the labelled set's: `bids migrate` copied those in for `eval`."""
    return bid_cache.prune(closed_before=now - timedelta(days=30), keep=labelled_b_ids(labelled))


def build_services(settings: Settings) -> tuple[Services, DocCache, list[Callable[[], Awaitable[None]]]]:
    if settings.tesseract_cmd:
        configure_tesseract(settings.tesseract_cmd)
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
        # Pages are ranked by meaning too: all-MiniLM-L6-v2 on the CPU by default, or a
        # remote embedder when EMBED_BASE_URL is set. It more than doubles the
        # chance the quoted page reaches the Judge's top 8 and costs seconds,
        # not the ~45 s a 112-page RFP takes with bge-m3 on the CPU.
        judge=Judge(model, cache, VerdictCache(settings.cache_dir, verdict_version(settings.llm_model)),
                    embedder=judge_embedder(settings),
                    reranker=judge_reranker(settings)),
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
        from gem_agent.cli import labelled_path

        log.info("bid cache: pruned %d bids closed over 30 days ago",
                 startup_prune(bid_cache, labelled_path(None), site_now()))

    return services, cache, [refresh_lookups, prune_bid_cache]
