"""Earlier rulings applied during a run: a borderline verdict that one of the user's rulings already answers."""
from __future__ import annotations

import logging

from gem_agent.agents.chat import short_name
from gem_agent.agents.schemas import MemoryFact, TenderRequest, TopicDefinition, Verdict
from gem_agent.graph.services import Services
from gem_agent.graph.store import Ruling

log = logging.getLogger(__name__)


def topic_for(request: TenderRequest, group: str) -> TopicDefinition:
    """The topic whose short name is the bid's group, else the first topic."""
    return next((t for t in request.topics if short_name(t) == group), request.topics[0])


async def settle(s: Services, user: str, topic: TopicDefinition, verdict: Verdict, *,
                 facts: list[MemoryFact] | None = None) -> Ruling | None:
    """Ask whether an earlier ruling decides this borderline bid. Never raises: a failed call leaves it borderline."""
    if s.rulings is None or not verdict.borderline:
        return None
    if facts is None:
        facts = s.memory.ruling_facts(user, short_name(topic))  # read now, so a ruling made mid-run counts
    if not facts:
        return None
    try:
        match = await s.rulings.resolve(topic, verdict.question, verdict.reason, facts)
    except Exception as exc:  # noqa: BLE001 - the bid stays borderline for the user
        log.warning("bid %s: could not match earlier rulings: %s", verdict.bid_number, exc)
        return None
    if match is None:
        return None
    fact = next((f for f in facts if f.id == match.fact_id), None)
    if fact is None:
        return None
    return Ruling(decision=match.decision, by="earlier", fact_id=fact.id, fact=fact.fact, at=s.now())
