"""Live GeM searches in two stages: page 1 of every search, then the rest (spec 6.2)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict

from gem_agent.cache.search_cache import SearchCache
from gem_agent.tools.gem.models import Bid
from gem_agent.tools.gem.search import (
    PAGE_SIZE,
    AllBidsQuery,
    CategorySearch,
    SearchPage,
    list_all_bids,
    search_bids,
)
from gem_agent.tools.http_client import BidplusSession, GemHttpError

log = logging.getLogger(__name__)


class SearchSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["category", "exact", "fullText"]
    value: str

    @property
    def key(self) -> str:
        return f"{self.kind}|{self.value}"


class SearchProgress(BaseModel):
    num_found: int
    pages_read: int
    page_count: int
    skipped: bool = False
    same_as: str | None = None
    failed: bool = False

    @property
    def pending(self) -> bool:
        """Pages are still to be read."""
        return not (self.skipped or self.failed or self.same_as) and self.pages_read < self.page_count


@dataclass
class SearchOutcome:
    spec: SearchSpec
    progress: SearchProgress
    bids: list[Bid]


class LiveSearcher:
    def __init__(self, session: BidplusSession, *, cache: SearchCache | None = None,
                 cached_keys: frozenset[str] = frozenset(), skip_over: int = 20000) -> None:
        self.session, self.cache, self.cached_keys, self.skip_over = session, cache, cached_keys, skip_over
        self._first_bids: dict[str, list[Bid]] = {}  # page-1 bids of base searches, to store them once complete

    async def _page(self, spec: SearchSpec, page: int) -> SearchPage:
        if spec.kind == "category":
            return await search_bids(self.session, CategorySearch(category=spec.value), page)
        return await list_all_bids(self.session, AllBidsQuery(search_text=spec.value, search_type=spec.kind), page)

    def _cacheable(self, spec: SearchSpec) -> bool:
        return self.cache is not None and spec.key in self.cached_keys

    async def first_pages(self, specs: list[SearchSpec]) -> list[SearchOutcome]:
        outcomes: list[SearchOutcome] = []
        seen: dict[tuple[int, tuple[int, ...]], str] = {}
        for spec in specs:
            outcomes.append(await self._first(spec, seen))
        return outcomes

    async def _first(self, spec: SearchSpec, seen: dict[tuple[int, tuple[int, ...]], str]) -> SearchOutcome:
        if self._cacheable(spec):
            hit = self.cache.get(spec.key)
            if hit is not None:
                num_found, docs = hit
                count = -(-num_found // PAGE_SIZE)
                return SearchOutcome(spec, SearchProgress(num_found=num_found, pages_read=count or 1, page_count=count),
                                     _dedupe(Bid.from_listing(d) for d in docs))
        try:
            page = await self._page(spec, 1)
        except (GemHttpError, httpx.HTTPError) as exc:
            log.warning("search %s failed on page 1: %s", spec.key, exc)
            return SearchOutcome(spec, SearchProgress(num_found=0, pages_read=0, page_count=0, failed=True), [])
        progress = SearchProgress(num_found=page.num_found, pages_read=1, page_count=page.page_count)
        if spec.kind == "fullText" and page.num_found > self.skip_over:
            progress.skipped = True
            return SearchOutcome(spec, progress, [])
        signature = (page.num_found, tuple(b.b_id for b in page.bids))
        if page.num_found and signature in seen:
            progress.same_as = seen[signature]
            return SearchOutcome(spec, progress, [])
        seen[signature] = spec.key
        bids = _dedupe(page.bids)
        if self._cacheable(spec):
            if page.page_count <= 1:
                self.cache.put(spec.key, page.num_found, [b.raw for b in bids])
            else:
                self._first_bids[spec.key] = bids
        return SearchOutcome(spec, progress, bids)

    async def rest(self, specs: list[SearchSpec], progress: dict[str, SearchProgress]) -> list[SearchOutcome]:
        """One outcome per spec, in order; a spec with nothing left to read comes back with no bids and its progress."""
        outcomes: list[SearchOutcome] = []
        for spec in specs:
            current = progress[spec.key]
            if not current.pending:
                outcomes.append(SearchOutcome(spec, current, []))
                continue
            outcomes.append(await self._rest(spec, current))
        return outcomes

    async def _rest(self, spec: SearchSpec, current: SearchProgress) -> SearchOutcome:
        new = current.model_copy()
        bids: list[Bid] = []
        for n in range(current.pages_read + 1, current.page_count + 1):
            try:
                page = await self._page(spec, n)
            except (GemHttpError, httpx.HTTPError) as exc:
                log.warning("search %s failed on page %d: %s", spec.key, n, exc)
                new.failed = True
                break
            bids.extend(page.bids)
            new.pages_read = n
        bids = _dedupe(bids)
        first = self._first_bids.pop(spec.key, None)
        if self._cacheable(spec) and first is not None and not new.failed:
            whole = _dedupe([*first, *bids])
            self.cache.put(spec.key, new.num_found, [b.raw for b in whole])
        return SearchOutcome(spec, new, bids)


def _dedupe(bids) -> list[Bid]:
    return list({b.b_id: b for b in bids}.values())
