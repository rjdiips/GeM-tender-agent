"""probe_site(meaning): how a candidate meaning fares against the open-bid titles (spec 5.3). No site requests."""
from __future__ import annotations

from gem_agent.agents.schemas import ProbeResult, ResolvedWindow, TopicDefinition
from gem_agent.index.db import BidIndex
from gem_agent.index.query import IndexQuery, search_index
from gem_agent.retrieval.bm25 import lexical_scores
from gem_agent.retrieval.embed import Embedder


def probe_site(index: BidIndex, embedder: Embedder, topic: TopicDefinition, window: ResolvedWindow,
               k: int = 8) -> ProbeResult:
    filters = window.model_dump()
    rows = index.candidates(**filters)
    terms = [*topic.match_terms, *topic.acronyms]
    strong = sum(1 for s in lexical_scores([r.text for r in rows], terms) if s > 0)
    hits = search_index(index, IndexQuery(text=topic.meaning, terms=terms, limit=k, **filters), embedder)
    return ProbeResult(meaning=topic.meaning, window_count=len(rows), strong=strong,
                       top_titles=[h.bid.title for h in hits])
