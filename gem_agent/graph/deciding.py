"""Pure routing and ordering for bids after the description check."""
from __future__ import annotations

import asyncio
from datetime import date
from typing import Literal

import numpy as np

from gem_agent.agents.matcher import Call
from gem_agent.agents.schemas import TopicDefinition
from gem_agent.agents.searcher import Candidate, from_own_search
from gem_agent.index.db import Description
from gem_agent.retrieval.embed import Embedder

Route = Literal["match", "unsure", "net", "today", "drop", "unseen"]


def screen_kept(c: Candidate) -> bool:
    """Whether today's title screen kept the bid (or never got to decide)."""
    return not c.why or c.why[0] != "title screen: no"


def route(c: Candidate, description: Description | None, call: Call | None, *, safety_net: bool) -> Route:
    """Choose one path for a bid. Similarity never reaches this decision."""
    if description is None or not description.usable() or call is None:
        return "today" if screen_kept(c) else "unseen"
    if call == "match":
        return "match"
    if call == "unsure":
        return "unsure"
    if not safety_net and description.has_scope:
        return "drop"
    return "net" if screen_kept(c) else "drop"


def describe_order(cands: list[Candidate], similarity: dict[int, float]) -> list[Candidate]:
    """Order description work: own searches, screen keeps, closing day, similarity, then bid id."""
    def key(c: Candidate) -> tuple:
        closing_day = c.bid.closing.date() if c.bid.closing else date.max
        return (not from_own_search(c), not screen_kept(c), closing_day,
                -similarity.get(c.bid.b_id, 0.0), c.bid.b_id)

    return sorted(cands, key=key)


def not_read_reason(description: Description | None, checks: list[str]) -> str:
    """Explain which checks ruled out a bid that was not fully read."""
    if len(checks) > 1:
        check_text = ", ".join(checks[:-1]) + " and " + checks[-1]
    else:
        check_text = "".join(checks)
    prefix = f"Not a match: buys {description.subject}." if description and description.usable() else "Not a match."
    return f"{prefix} Ruled out by {check_text}."


async def title_similarity(embedder: Embedder | None, topic: TopicDefinition, titles: list[str]) -> list[float]:
    """Cosine similarity between the topic and each title, without blocking the event loop."""
    if embedder is None:
        return [0.0] * len(titles)
    topic_text = topic.meaning + "; " + "; ".join(topic.match_terms)
    vectors = np.asarray(await asyncio.to_thread(embedder.encode, [topic_text, *titles]), dtype=np.float32)
    unit = vectors / np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
    return (unit[1:] @ unit[0]).tolist()
