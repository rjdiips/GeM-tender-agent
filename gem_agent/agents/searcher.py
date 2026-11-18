"""Searcher (spec 5.2): a ranked shortlist for one topic, from the local index, with no site requests except the
optional live category searches.

1. Pool: every title that matches the topic's terms (word rules of 5.3), plus the closest titles by meaning.
2. Title screen: one batched model call keeps every plausible title. On 16 Nov the reranker could not separate
   relevant titles (for AML its top hit was "SOFTWARES") and costs 10-18 s per query on CPU, so the screen replaces
   it; it stays available when an embedder runs on the GPU.
3. Vague titles (a bare service category such as "Hiring of Agency for IT Projects- Milestone basis") say nothing
   about the subject. Once the index holds the bid's card (what it buys, written from its bid document; index/cards.py)
   the screen sees the card; until then the bid is never dropped: only its documents can tell.
4. Closing soonest first; preferred buyers first within a day.
"""
from __future__ import annotations

import logging
from datetime import datetime

from pydantic import BaseModel, Field
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.models import Model

from gem_agent.agents.chat import short_name
from gem_agent.agents.schemas import SearchPlan, TopicDefinition
from gem_agent.index.db import BidIndex
from gem_agent.index.query import IndexQuery, search_index
from gem_agent.retrieval.embed import Embedder
from gem_agent.tools.gem.models import Bid, site_now
from gem_agent.tools.gem.search import CategorySearch, search_bids_all
from gem_agent.tools.gem.titles import is_vague_title
from gem_agent.tools.http_client import BidplusSession

log = logging.getLogger(__name__)

SCREEN_INSTRUCTIONS = """You screen Indian government (GeM) tender titles for one topic, before anyone reads the documents.
Keep every tender whose title could plausibly be about the topic, including broad or generic titles that might
contain it. Drop only titles that are plainly about something else. Look-alikes listed under "NOT this" are dropped.
Answer only with the bracketed numbers of the titles to keep: no explanations."""

class SearchSettings(BaseModel):
    semantic_pool: int = 60  # closest titles by meaning added to the word matches
    word_pool: int = 300
    screen_batch: int = 40
    # 40 numbers as pretty-printed JSON can pass 300 tokens: on 16 Nov 22:18 the screen was cut off and all 61 titles
    # went to triage unscreened. A higher cap costs nothing unless the model needs it.
    screen_max_tokens: int = 600
    cap: int = 150  # more than this goes back to the Planner to narrow (spec 5.1)
    category_pages: int = 20


class Candidate(BaseModel):
    bid: Bid
    group: str  # the topic it was found for
    why: list[str]


class SearchResult(BaseModel):
    candidates: list[Candidate]
    pool: int  # titles looked at
    over_cap: bool = False


class ScreenOutput(BaseModel):
    # Numbers only. A free-text "reason" here ran past max_tokens on 16 Nov and failed two runs with cut-off JSON.
    keep: list[int] = Field(description="bracketed numbers of the titles to keep")


class Searcher:
    def __init__(self, model: Model, index: BidIndex, embedder: Embedder, *, session: BidplusSession | None = None,
                 settings: SearchSettings | None = None) -> None:
        self.index, self.embedder, self.session = index, embedder, session
        self.settings = settings or SearchSettings()
        self._screen = Agent(model, output_type=NativeOutput(ScreenOutput), instructions=SCREEN_INSTRUCTIONS)

    async def search(self, topic: TopicDefinition, plan: SearchPlan) -> SearchResult:
        s = self.settings
        window = {"published_from": plan.published_from, "published_to": plan.published_to,
                  "closing_from": plan.closing_from, "closing_to": plan.closing_to,
                  "ministries": plan.ministries, "departments": plan.departments, "bid_types": plan.bid_types}
        pool: dict[int, tuple[Bid, list[str]]] = {}
        for hit in search_index(self.index, IndexQuery(terms=plan.terms, limit=s.word_pool, **window), self.embedder):
            pool[hit.bid.b_id] = (hit.bid, ["title words"])
        for hit in search_index(self.index, IndexQuery(text=plan.text, limit=s.semantic_pool, **window), self.embedder):
            pool.setdefault(hit.bid.b_id, (hit.bid, []))[1].append("close in meaning")
        for bid in await self._category_bids(plan):
            pool.setdefault(bid.b_id, (bid, []))[1].append("category search")
        for bid in self.index.vague_without_card(**window):
            pool.setdefault(bid.b_id, (bid, []))[1].append("no subject found in its documents yet")
        if not pool:
            return SearchResult(candidates=[], pool=0)

        bids = [b for b, _ in pool.values()]
        cards = {b.b_id: c for b in bids if is_vague_title(b.title) and (c := self.index.card(b.b_id))}
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
        out.sort(key=lambda c: (c.bid.closing or datetime.max).date().isoformat() +
                 ("0" if "preferred buyer" in c.why else "1") + (c.bid.closing or datetime.max).isoformat())
        return SearchResult(candidates=out[: s.cap], pool=len(pool), over_cap=len(out) > s.cap)

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

    async def _category_bids(self, plan: SearchPlan) -> list[Bid]:
        """Live searches for the plan's categories (spec 5.2), filtered to the window locally and stored in the index.

        No date filter goes to the site: GeM rejects a half-open end-date range with 422 (16 Nov), and the window
        is applied here anyway. A category search that fails is skipped: it is an optional extra, never the run.
        """
        if self.session is None or not plan.categories:
            return []
        found: list[Bid] = []
        for category in plan.categories:
            try:
                result = await search_bids_all(self.session, CategorySearch(category=category),
                                               max_pages=self.settings.category_pages)
            except Exception as exc:  # noqa: BLE001 - HTTP errors, unreadable pages: skip this category
                log.warning("category search %s failed: %s", category, exc)
                continue
            found.extend(result.bids)
        if found:
            self.index.upsert(found, seen_at=site_now())
        lo, hi = plan.published_from, plan.published_to
        clo, chi = plan.closing_from, plan.closing_to
        return [b for b in found
                if b.published and (not lo or b.published >= lo) and (not hi or b.published <= hi)
                and (not clo or (b.closing and b.closing >= clo)) and (not chi or (b.closing and b.closing <= chi))]
