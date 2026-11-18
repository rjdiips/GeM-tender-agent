"""Reciprocal rank fusion."""
from __future__ import annotations

from collections.abc import Hashable, Sequence
from typing import TypeVar

K = TypeVar("K", bound=Hashable)


def reciprocal_rank_fusion(rankings: Sequence[Sequence[K]], k: int = 60) -> dict[K, float]:
    """Fused score per item, best first. Each ranking lists items best first."""
    scores: dict[K, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return dict(sorted(scores.items(), key=lambda kv: kv[1], reverse=True))
