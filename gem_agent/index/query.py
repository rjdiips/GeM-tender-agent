"""Date/buyer/type filters, semantic + word ranking, rerank, shortlist (spec 6.2)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from gem_agent.index.db import BidIndex, CandidateRow
from gem_agent.retrieval.bm25 import lexical_scores
from gem_agent.retrieval.embed import Embedder
from gem_agent.retrieval.fusion import reciprocal_rank_fusion
from gem_agent.retrieval.rerank import Reranker
from gem_agent.tools.gem.models import Bid


class IndexQuery(BaseModel):
    text: str = ""  # the topic's expanded meaning, never a bare acronym (spec 5.3)
    terms: list[str] = []  # word-search terms, matched with the rules in retrieval.bm25
    published_from: datetime | None = None
    published_to: datetime | None = None
    closing_from: datetime | None = None
    closing_to: datetime | None = None
    ministries: list[str] = []
    departments: list[str] = []
    bid_types: list[int] = []
    include_closed: bool = False
    rerank_top: int = 200
    limit: int = 150


class Hit(BaseModel):
    bid: Bid
    semantic: float | None = None
    lexical: float = 0.0
    fused: float = 0.0
    rerank: float | None = None


def search_index(
    index: BidIndex, q: IndexQuery, embedder: Embedder, reranker: Reranker | None = None
) -> list[Hit]:
    """Ranked open bids for a query, from the local index only (no site requests)."""
    rows = index.candidates(
        published_from=q.published_from, published_to=q.published_to, closing_from=q.closing_from,
        closing_to=q.closing_to, ministries=q.ministries, departments=q.departments, bid_types=q.bid_types,
        include_closed=q.include_closed,
    )
    if not rows:
        return []
    if not q.text and not q.terms:
        rows.sort(key=lambda r: (r.closing is None, r.closing or ""))
        return [Hit(bid=r.bid()) for r in rows[: q.limit]]

    by_id: dict[int, CandidateRow] = {r.b_id: r for r in rows}
    rankings: list[list[int]] = []
    semantic: dict[int, float] = {}
    if q.text:
        ids, matrix = index.vectors(embedder.model_name)
        if len(ids):
            position = {int(b): i for i, b in enumerate(ids)}
            query_vec = embedder.encode([q.text])[0]
            present = [b for b in by_id if b in position]
            if present:
                sims = matrix[[position[b] for b in present]] @ query_vec
                semantic = dict(zip(present, (float(s) for s in sims), strict=True))
                rankings.append(sorted(semantic, key=semantic.__getitem__, reverse=True))
    lexical: dict[int, float] = {}
    if q.terms:
        scores = lexical_scores([r.text for r in rows], q.terms)
        lexical = {r.b_id: s for r, s in zip(rows, scores, strict=True) if s > 0}
        rankings.append(sorted(lexical, key=lexical.__getitem__, reverse=True))

    fused = reciprocal_rank_fusion(rankings)
    ordered = list(fused)
    rerank: dict[int, float] = {}
    if reranker and ordered:
        top = ordered[: q.rerank_top]
        scores = reranker.score(q.text or " ".join(q.terms), [by_id[b].text for b in top])
        rerank = dict(zip(top, scores, strict=True))
        top.sort(key=lambda b: (rerank[b], fused[b]), reverse=True)
        ordered = top + ordered[q.rerank_top :]
    return [
        Hit(bid=by_id[b].bid(), semantic=semantic.get(b), lexical=lexical.get(b, 0.0), fused=fused[b],
            rerank=rerank.get(b))
        for b in ordered[: q.limit]
    ]
