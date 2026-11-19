"""Searcher (spec 5.2, 6.2): a ranked shortlist for one topic, from live GeM searches.

1. Searches: the base searches, then the plan's categories, buyers and terms (search_specs). Stage "first" reads page 1
   of each, stage "rest" the remaining pages (tools/gem/live.py).
2. Pool: the bids of every search merged by b_id, minus those an earlier stage already returned, cut to the plan's window
   and hard buyer filters locally.
3. Title screen: one batched model call keeps every plausible title.
4. Vague titles (a bare service category such as "Hiring of Agency for IT Projects- Milestone basis") say nothing
   about the subject. Once the bid cache holds the bid's card (what it buys, written from its bid document) the screen
   sees the card; until then the bid is never dropped: only its documents can tell.
5. Closing soonest first; preferred buyers first within a day.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.models import Model

from gem_agent.agents.chat import short_name
from gem_agent.agents.schemas import SearchPlan, TopicDefinition
from gem_agent.index.db import BidIndex
from gem_agent.tools.gem.live import LiveSearcher, SearchProgress, SearchSpec
from gem_agent.tools.gem.models import Bid, site_now
from gem_agent.tools.gem.titles import is_vague_title

log = logging.getLogger(__name__)

BASE_SPECS: list[SearchSpec] = [
    SearchSpec(kind="category", value="services_home_itse_hi12433188"),
    SearchSpec(kind="exact", value="Department of Financial Services"),
    SearchSpec(kind="exact", value="BANKING SECTOR"),
    SearchSpec(kind="fullText", value="consultants"),
]


def search_specs(plan: SearchPlan) -> list[SearchSpec]:
    """The base searches, then the plan's categories, buyers (hard and preferred) and terms; no repeats, order kept."""
    wanted = [
        *BASE_SPECS,
        *(SearchSpec(kind="category", value=c) for c in plan.categories),
        *(SearchSpec(kind="exact", value=b)
          for b in [*plan.ministries, *plan.departments, *plan.prefer_ministries, *plan.prefer_departments]),
        *(SearchSpec(kind="fullText", value=t) for t in plan.terms),
    ]
    return list(dict.fromkeys(wanted))


SCREEN_INSTRUCTIONS = """You screen Indian government (GeM) tender titles for one topic, before anyone reads the documents.
Keep every tender whose title could plausibly be about the topic, including broad or generic titles that might
contain it. Drop only titles that are plainly about something else. Look-alikes listed under "NOT this" are dropped.
Answer only with the bracketed numbers of the titles to keep: no explanations."""

class SearchSettings(BaseModel):
    screen_batch: int = 40
    # 40 numbers as pretty-printed JSON can pass 300 tokens: on 16 Nov 22:18 the screen was cut off and all 61 titles
    # went to triage unscreened. A higher cap costs nothing unless the model needs it.
    screen_max_tokens: int = 600
    cap: int = 150  # per topic, across both stages; more than this goes back to the Planner to narrow (spec 5.1)


class Candidate(BaseModel):
    bid: Bid
    group: str  # the topic it was found for
    why: list[str]


class SearchResult(BaseModel):
    candidates: list[Candidate]
    pool: int  # titles looked at
    over_cap: bool = False
    cut: int = 0  # kept titles left out by the cap: never read, so the user is told
    progress: dict[str, SearchProgress] = {}  # per search key, to resume at the second stage


class ScreenOutput(BaseModel):
    # Numbers only. A free-text "reason" here ran past max_tokens on 16 Nov and failed two runs with cut-off JSON.
    keep: list[int] = Field(description="bracketed numbers of the titles to keep")


class Searcher:
    def __init__(self, model: Model, bid_cache: BidIndex, live: LiveSearcher, *,
                 settings: SearchSettings | None = None) -> None:
        self.bid_cache, self.live = bid_cache, live
        self.settings = settings or SearchSettings()
        self._screen = Agent(model, output_type=NativeOutput(ScreenOutput), instructions=SCREEN_INSTRUCTIONS)

    async def search(self, topic: TopicDefinition, plan: SearchPlan, *, stage: Literal["first", "rest"] = "first",
                     progress: dict[str, SearchProgress] | None = None,
                     exclude: set[int] = frozenset(),  # type: ignore[assignment]
                     cap: int | None = None) -> SearchResult:
        """`cap` overrides the settings' cap: stage 2 gets what stage 1 left of the topic's cap."""
        cap = self.settings.cap if cap is None else max(0, cap)
        specs = search_specs(plan)
        if stage == "first":
            outcomes = await self.live.first_pages(specs)
        else:
            outcomes = await self.live.rest(specs, progress or {})
        new_progress = {**(progress or {}), **{o.spec.key: o.progress for o in outcomes}}
        # every buyer the listings name, kept or not, widens the Planner's buyer choices (cache/lookups.py)
        self.bid_cache.add_buyers(b for o in outcomes for b in o.bids)

        pool: dict[int, tuple[Bid, list[str]]] = {}
        for outcome in outcomes:
            for bid in outcome.bids:
                if bid.b_id in exclude or not self._in_scope(bid, plan):
                    continue
                pool.setdefault(bid.b_id, (bid, []))[1].append(outcome.spec.key)
        if not pool:
            return SearchResult(candidates=[], pool=0, progress=new_progress)

        bids = [b for b, _ in pool.values()]
        cards = {b.b_id: c for b in bids if is_vague_title(b.title) and (c := self.bid_cache.card(b.b_id))}
        vague = {b.b_id for b in bids if is_vague_title(b.title) and b.b_id not in cards}
        kept = await self._screen_titles(topic, [b for b in bids if b.b_id not in vague], cards)
        preferred = set(plan.prefer_ministries) | set(plan.prefer_departments)
        out = []
        for bid, why in pool.values():
            if bid.b_id in vague:
                why = ["vague title: only its documents can tell", *why]
            elif kept is not None and bid.b_id not in kept:
                continue
            elif kept is None:
                why = ["screen failed: title kept for triage", *why]
            if preferred & {bid.ministry, bid.department}:
                why = [*why, "preferred buyer"]
            out.append(Candidate(bid=bid, group=short_name(topic), why=why))
        self.bid_cache.upsert([c.bid for c in out], seen_at=site_now())
        out.sort(key=lambda c: (c.bid.closing or datetime.max).date().isoformat() +
                 ("0" if "preferred buyer" in c.why else "1") + (c.bid.closing or datetime.max).isoformat())
        cut = max(0, len(out) - cap)
        return SearchResult(candidates=out[:cap], pool=len(pool), over_cap=cut > 0, cut=cut, progress=new_progress)

    @staticmethod
    def _in_scope(bid: Bid, plan: SearchPlan) -> bool:
        """The plan's window and hard buyer filters, applied here: no date filter goes to the site (GeM rejects a
        half-open end-date range with 422, 16 Nov)."""
        lo, hi = plan.published_from, plan.published_to
        clo, chi = plan.closing_from, plan.closing_to
        if lo or hi:
            if not bid.published or (lo and bid.published < lo) or (hi and bid.published > hi):
                return False
        if (clo or chi) and not bid.closing:
            return False
        if (clo and bid.closing < clo) or (chi and bid.closing > chi):
            return False
        if plan.ministries and bid.ministry not in plan.ministries:
            return False
        if plan.departments and bid.department not in plan.departments:
            return False
        return not plan.bid_types or int(bid.bid_type) in plan.bid_types

    async def _screen_titles(self, topic: TopicDefinition, bids: list[Bid],
                             cards: dict[int, str] | None = None) -> set[int] | None:
        """The titles to keep, or None if the model could not answer: then nothing is dropped and triage decides
        from the documents (one bad answer must not fail the run)."""
        kept: set[int] = set()
        for start in range(0, len(bids), self.settings.screen_batch):
            batch = bids[start : start + self.settings.screen_batch]
            cards = cards or {}
            listing = "\n".join(
                f"[{i}] {b.title[:200]}{f' — what it buys: {cards[b.b_id][:300]}' if b.b_id in cards else ''}"
                f" | {b.ministry or ''} / {b.department or ''}" for i, b in enumerate(batch))
            try:
                out: ScreenOutput = (await self._screen.run(
                    f"{topic.render()}\n\nTenders:\n{listing}",
                    model_settings={"max_tokens": self.settings.screen_max_tokens, "temperature": 0.0})).output
            except Exception as exc:  # noqa: BLE001 - validation failure after retries, timeouts, …
                log.warning("title screen failed (%s); keeping all %d titles for triage", exc, len(bids))
                return None
            kept |= {batch[i].b_id for i in out.keep if 0 <= i < len(batch)}
        return kept
