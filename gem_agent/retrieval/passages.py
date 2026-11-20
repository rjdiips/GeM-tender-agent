"""Find the passages of one bid that bear on the topic (spec 5.4 step 3).

Word search (BM25 with the 5.3 rules) always runs. Dense search and the reranker run only when given: on CPU
they cost ~2.5 pages/s and ~9 s per 24 pages, so they are used when the embedding device is the GPU (spec 3.5).
A remote model server that is down must not fail the bid, so both are best-effort.
"""
from __future__ import annotations

import logging

from gem_agent.retrieval.bm25 import lexical_scores
from gem_agent.retrieval.chunks import Chunk
from gem_agent.retrieval.embed import Embedder
from gem_agent.retrieval.fusion import reciprocal_rank_fusion
from gem_agent.retrieval.rerank import Reranker

log = logging.getLogger(__name__)


def find_passages(
    chunks: list[Chunk],
    *,
    query: str,
    terms: list[str],
    top_k: int = 8,
    embedder: Embedder | None = None,
    reranker: Reranker | None = None,
    rerank_pool: int = 24,
) -> list[Chunk]:
    if not chunks:
        return []
    rankings: list[list[int]] = []
    scores = lexical_scores([c.text for c in chunks], terms)
    lexical = [i for i in sorted(range(len(chunks)), key=lambda i: -scores[i]) if scores[i] > 0]
    rankings.append(lexical)
    if embedder is not None and query:
        try:
            vectors = embedder.encode([c.text for c in chunks])
            sims = vectors @ embedder.encode([query])[0]
            rankings.append(sorted(range(len(chunks)), key=lambda i: -float(sims[i])))
        except Exception as exc:  # noqa: BLE001 - a dead remote server must not lose the bid; words still rank it
            log.warning("dense search failed, words only: %s", exc)
    ordered = list(reciprocal_rank_fusion(rankings))
    if reranker is not None and ordered and query:
        try:
            pool = ordered[:rerank_pool]
            rr = reranker.score(query, [chunks[i].text for i in pool])
            ordered = [i for _, i in sorted(zip(rr, pool, strict=True), key=lambda t: -t[0])]
        except Exception as exc:  # noqa: BLE001 - keep the fused order
            log.warning("reranking failed, fused order kept: %s", exc)
    return [chunks[i] for i in ordered[:top_k]]
