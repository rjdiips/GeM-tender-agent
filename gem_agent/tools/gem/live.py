"""Live GeM searches in two stages: page 1 of every search, then the rest."""
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
from gem_agent.tools.http_client import BidplusSession, GemHttpError, HostNotAllowed

log = logging.getLogger(__name__)

# A page can fail at the HTTP level or arrive as a body parse_page cannot read (HTML error page, missing keys, bad doc).
# ValueError covers bad numbers and pydantic validation. HostNotAllowed is a ValueError too, so every handler re-raises it
# first: an off-allowlist redirect must surface, not become a failed search.
_READ_ERRORS = (GemHttpError, httpx.HTTPError, ValueError, KeyError, TypeError)


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
    first_ids: list[int] = []  # page 1's bids, for a base search: stage 2 leaves them out when it reads the cache

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
        # page-1 bids of base searches, to store each search once it is complete. Keyed by page 1's bids too, so
        # concurrent runs that saw different first pages never take each other's.
        self._first_bids: dict[tuple[str, tuple[int, ...]], list[Bid]] = {}

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

    def _cached(self, spec: SearchSpec) -> tuple[int, list[Bid]] | None:
        """A fresh, readable cache entry for a base search: (num_found, bids); else None."""
        if not self._cacheable(spec):
            return None
        hit = self.cache.get(spec.key)
        if hit is None:
            return None
        num_found, docs = hit
        try:
            return num_found, _dedupe(Bid.from_listing(d) for d in docs)
        except _READ_ERRORS as exc:  # a corrupt cache entry is a miss
            log.warning("cached search %s is unreadable, reading it live: %s", spec.key, exc)
            return None

    @staticmethod
    def _complete(num_found: int, first_ids: list[int] | None = None) -> SearchProgress:
        count = -(-num_found // PAGE_SIZE)
        return SearchProgress(num_found=num_found, pages_read=count or 1, page_count=count, first_ids=first_ids or [])

    async def _first(self, spec: SearchSpec, seen: dict[tuple[int, tuple[int, ...]], str]) -> SearchOutcome:
        if (hit := self._cached(spec)) is not None:
            num_found, bids = hit
            return SearchOutcome(spec, self._complete(num_found), bids)
        try:
            page = await self._page(spec, 1)
        except HostNotAllowed:
            raise
        except _READ_ERRORS as exc:
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
                progress.first_ids = [b.b_id for b in bids]
                self._first_bids[(spec.key, tuple(progress.first_ids))] = bids
        return SearchOutcome(spec, progress, bids)

    async def rest(self, specs: list[SearchSpec], progress: dict[str, SearchProgress]) -> list[SearchOutcome]:
        """One outcome per spec, in order; a spec with nothing left to read comes back with no bids and its progress.
        A base search another topic (or run) completed within the hour comes from the cache: its bids minus this
        search's page 1, which stage 1 already returned."""
        outcomes: list[SearchOutcome] = []
        for spec in specs:
            current = progress[spec.key]
            if not current.pending:
                outcomes.append(SearchOutcome(spec, current, []))
                continue
            if (hit := self._cached(spec)) is not None:
                num_found, bids = hit
                self._first_bids.pop((spec.key, tuple(current.first_ids)), None)
                first = set(current.first_ids)
                outcomes.append(SearchOutcome(spec, self._complete(num_found, current.first_ids),
                                              [b for b in bids if b.b_id not in first]))
                continue
            outcomes.append(await self._rest(spec, current))
        return outcomes

    async def _rest(self, spec: SearchSpec, current: SearchProgress) -> SearchOutcome:
        new = current.model_copy()
        bids: list[Bid] = []
        for n in range(current.pages_read + 1, current.page_count + 1):
            try:
                page = await self._page(spec, n)
            except HostNotAllowed:
                raise
            except _READ_ERRORS as exc:
                log.warning("search %s failed on page %d: %s", spec.key, n, exc)
                new.failed = True
                break
            bids.extend(page.bids)
            new.pages_read = n
        bids = _dedupe(bids)
        first = self._first_bids.pop((spec.key, tuple(current.first_ids)), None)
        if self._cacheable(spec) and first is not None and not new.failed:
            whole = _dedupe([*first, *bids])
            self.cache.put(spec.key, new.num_found, [b.raw for b in whole])
        return SearchOutcome(spec, new, bids)


def _dedupe(bids) -> list[Bid]:
    return list({b.b_id: b for b in bids}.values())
