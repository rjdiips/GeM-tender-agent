"""Searcher: a ranked shortlist for one topic, from live GeM searches.

1. Searches: the base searches, then the plan's categories, buyers and terms (search_specs). Stage "first" reads page 1
   of each, stage "rest" the remaining pages (tools/gem/live.py).
2. Pool: the bids of every search merged by b_id, minus those an earlier stage already returned, cut to the plan's window
   and hard buyer filters locally.
3. Title screen: one batched model call keeps every plausible title.
4. Vague titles (a bare service category such as "Hiring of Agency for IT Projects- Milestone basis") say nothing
   about the subject. For those titles only, a usable description from the running model and prompt (what the bid
   document says it buys) is shown to the screen; without one the bid is never dropped by the title screen. Screen
   drops and cap cuts return separately in rest_pool for the description check.
5. Closing soonest first, after the bids the topic's own terms found (a base search alone fills the cap with banking
   IT bids for any topic); preferred buyers first within a day.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.models import Model

from gem_agent.agents.chat import short_name
from gem_agent.agents.schemas import SearchPlan, TopicDefinition
from gem_agent.index.db import BidIndex, buyer_name
from gem_agent.index.describe import DESCRIBE_PROMPT_VERSION
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
BASE_KEYS = frozenset(s.key for s in BASE_SPECS)


def from_own_search(c: Candidate) -> bool:
    """Whether one of the topic's own searches found it. A base search alone finds banking IT bids whatever the
    topic, so its bids go after the topic's own when the cap cuts the shortlist."""
    return any("|" in w and w not in BASE_KEYS for w in c.why)


def _names(values: list[str]) -> list[str]:
    """Buyer names normalised as the Planner was offered them (index.db.buyer_name); unsafe ones dropped."""
    return [c for v in values if (c := buyer_name(v))]


def search_specs(plan: SearchPlan) -> list[SearchSpec]:
    """The base searches, then the plan's categories, buyers (hard and preferred) and terms; no repeats, order kept."""
    wanted = [
        *BASE_SPECS,
        *(SearchSpec(kind="category", value=c) for c in plan.categories),
        *(SearchSpec(kind="exact", value=b)
          for b in _names([*plan.ministries, *plan.departments, *plan.prefer_ministries, *plan.prefer_departments])),
        *(SearchSpec(kind="fullText", value=t) for t in plan.terms),
    ]
    return list(dict.fromkeys(wanted))


def in_scope(bid: Bid, plan: SearchPlan) -> bool:
    """The plan's window and hard buyer filters, applied locally: no date filter goes to the site (GeM rejects a
    half-open end-date range with 422). Shared by the Searcher and the earlier live probe."""
    lo, hi = plan.published_from, plan.published_to
    clo, chi = plan.closing_from, plan.closing_to
    if (lo or hi) and (not bid.published or (lo and bid.published < lo) or (hi and bid.published > hi)):
        return False
    if (clo or chi) and not bid.closing:
        return False
    if (clo and bid.closing < clo) or (chi and bid.closing > chi):
        return False
    # both sides normalised: the Planner chose from names cut to 120 characters, the listing carries them whole
    if plan.ministries and buyer_name(bid.ministry) not in _names(plan.ministries):
        return False
    if plan.departments and buyer_name(bid.department) not in _names(plan.departments):
        return False
    return not plan.bid_types or int(bid.bid_type) in plan.bid_types


SCREEN_INSTRUCTIONS = """You screen Indian government (GeM) tender titles for one topic, before anyone reads the documents.
Keep every tender whose title could plausibly be about the topic, including broad or generic titles that might
contain it. Drop only titles that are plainly about something else. Look-alikes listed under "NOT this" are dropped.
Answer only with the bracketed numbers of the titles to keep: no explanations."""

class SearchSettings(BaseModel):
    screen_batch: int = 40
    # 40 numbers as pretty-printed JSON can pass 300 tokens. A higher cap costs nothing unless the model needs it.
    screen_max_tokens: int = 600
    cap: int = 150  # per topic, across both stages; more than this goes back to the Planner to narrow


class Candidate(BaseModel):
    bid: Bid
    group: str  # display short name; not a unique topic identity
    why: list[str]
    topic_key: str | None = None  # TopicDefinition.key(); None keeps old checkpoints readable

    @property
    def identity(self) -> str:
        return self.topic_key or self.group


class SearchResult(BaseModel):
    candidates: list[Candidate]
    rest_pool: list[Candidate] = []  # in-window bids outside candidates: title-screen drops and cap cuts
    pool: int  # titles looked at
    over_cap: bool = False
    cut: int = 0  # kept titles left out by the cap: never read, so the user is told
    progress: dict[str, SearchProgress] = {}  # per search key, to resume at the second stage


class ScreenOutput(BaseModel):
    # Numbers only: a free-text "reason" can run past max_tokens and fail the call with cut-off JSON.
    keep: list[int] = Field(description="bracketed numbers of the titles to keep")


class Searcher:
    def __init__(self, model: Model, bid_cache: BidIndex, live: LiveSearcher, *, description_model: str,
                 description_prompt_version: str = DESCRIBE_PROMPT_VERSION,
                 settings: SearchSettings | None = None) -> None:
        self.bid_cache, self.live = bid_cache, live
        self.description_model = description_model
        self.description_prompt_version = description_prompt_version
        self.settings = settings or SearchSettings()
        self._screen = Agent(model, output_type=NativeOutput(ScreenOutput), instructions=SCREEN_INSTRUCTIONS)

    async def search(self, topic: TopicDefinition, plan: SearchPlan, *, stage: Literal["first", "rest"] = "first",
                     progress: dict[str, SearchProgress] | None = None,
                     exclude: set[int] = frozenset(),  # type: ignore[assignment]
                     cap: int | None = None,
                     on_page: Callable[[int, int], None] | None = None) -> SearchResult:
        """`cap` overrides the settings' cap: stage 2 gets what stage 1 left of the topic's cap.
        `on_page(bids found, budget)`: stage 2 only, for a live count."""
        cap = self.settings.cap if cap is None else max(0, cap)
        specs = search_specs(plan)
        if stage == "first":
            outcomes = await self.live.first_pages(specs)
        else:
            # stage 2 only tops up a shortlist: three times the places left is enough, and every page costs a second
            outcomes = await self.live.rest(
                specs, progress or {}, budget=cap * 3, on_page=on_page,
                window_start=plan.published_from, keep=lambda b: in_scope(b, plan))
        new_progress = {**(progress or {}), **{o.spec.key: o.progress for o in outcomes}}

        pool: dict[int, tuple[Bid, list[str]]] = {}
        for outcome in outcomes:
            for bid in outcome.bids:
                if bid.b_id in exclude or not in_scope(bid, plan):
                    continue
                pool.setdefault(bid.b_id, (bid, []))[1].append(outcome.spec.key)
        # the buyers of every bid in scope, screened in or not, widen the Planner's buyer choices (cache/lookups.py)
        self.bid_cache.add_buyers(b for b, _ in pool.values())
        if not pool:
            return SearchResult(candidates=[], pool=0, progress=new_progress)

        bids = [b for b, _ in pool.values()]
        vague = {b.b_id for b in bids if is_vague_title(b.title)}
        descriptions = {}
        for b_id in vague:
            if ((description := self.bid_cache.description(b_id)) is not None and description.usable()
                    and description.current(self.description_model, self.description_prompt_version)):
                descriptions[b_id] = description.text()
        vague -= descriptions.keys()
        kept, unscreened = await self._screen_titles(topic, [b for b in bids if b.b_id not in vague], descriptions)
        preferred = set(plan.prefer_ministries) | set(plan.prefer_departments)
        group = short_name(topic)
        out: list[Candidate] = []
        rest_pool: list[Candidate] = []
        for bid, why in pool.values():
            if bid.b_id in vague:
                why = ["vague title: only its documents can tell", *why]
            elif bid.b_id in unscreened:
                why = ["screen failed: title kept for triage", *why]
            elif bid.b_id not in kept:
                rest_pool.append(Candidate(bid=bid, group=group, topic_key=topic.key(),
                                           why=["title screen: no", *why]))
                continue
            if preferred & {bid.ministry, bid.department}:
                why = [*why, "preferred buyer"]
            out.append(Candidate(bid=bid, group=group, topic_key=topic.key(), why=why))
        self.bid_cache.upsert(bids, seen_at=site_now())
        out.sort(key=lambda c: ("0" if from_own_search(c) else "1") +  # the topic's own hits get the cap's places first
                 (c.bid.closing or datetime.max).date().isoformat() +
                 ("0" if "preferred buyer" in c.why else "1") + (c.bid.closing or datetime.max).isoformat())
        cut = max(0, len(out) - cap)
        rest_pool.extend(Candidate(bid=c.bid, group=c.group, topic_key=c.topic_key,
                                   why=["over the cap", *c.why]) for c in out[cap:])
        return SearchResult(candidates=out[:cap], rest_pool=rest_pool, pool=len(pool), over_cap=cut > 0, cut=cut,
                            progress=new_progress)

    async def _screen_titles(self, topic: TopicDefinition, bids: list[Bid],
                             descriptions: dict[int, str] | None = None) -> tuple[set[int], set[int]]:
        """The titles to keep, and those left unscreened: a batch the model fails on is split in two and each half
        tried once; only a half that fails again goes to triage (one bad answer must not fail the run)."""
        kept: set[int] = set()
        unscreened: set[int] = set()
        for start in range(0, len(bids), self.settings.screen_batch):
            batch = bids[start : start + self.settings.screen_batch]
            if (found := await self._screen_batch(topic, batch, descriptions)) is not None:
                kept |= found
                continue
            half = (len(batch) + 1) // 2
            for part in (batch[:half], batch[half:]):
                if not part:
                    continue
                if (found := await self._screen_batch(topic, part, descriptions)) is not None:
                    kept |= found
                else:
                    unscreened |= {b.b_id for b in part}
        return kept, unscreened

    async def _screen_batch(self, topic: TopicDefinition, batch: list[Bid],
                            descriptions: dict[int, str] | None) -> set[int] | None:
        """The b_ids the model keeps from one batch, or None if it could not answer."""
        descriptions = descriptions or {}
        listing = "\n".join(
            f"[{i}] {b.title[:200]}{f' — what it buys: {descriptions[b.b_id][:300]}' if b.b_id in descriptions else ''}"
            f" | {b.ministry or ''} / {b.department or ''}" for i, b in enumerate(batch))
        try:
            out: ScreenOutput = (await self._screen.run(
                f"{topic.render()}\n\nTenders:\n{listing}",
                model_settings={"max_tokens": self.settings.screen_max_tokens, "temperature": 0.0})).output
        except Exception as exc:  # noqa: BLE001 - validation failure after retries, timeouts, …
            log.warning("title screen failed (%s) for %d titles", exc, len(batch))
            return None
        return {batch[i].b_id for i in out.keep if 0 <= i < len(batch)}
