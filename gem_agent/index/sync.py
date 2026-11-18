"""First full load, 15-min incremental sync, nightly full re-fetch, embedding of new titles (spec 6.2)."""
from __future__ import annotations

import fcntl
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from gem_agent.index.db import BidIndex
from gem_agent.retrieval.embed import Embedder
from gem_agent.tools.gem.models import site_now
from gem_agent.tools.gem.search import AllBidsQuery, list_all_bids
from gem_agent.tools.http_client import BidplusSession

log = logging.getLogger(__name__)

NEXT_PAGE = "full_load_next_page"
FULL_STARTED = "full_load_started"
LAST_SYNC = "last_sync_started"
NUM_FOUND = "listing_num_found"
NEWEST_FIRST = AllBidsQuery(sort="Bid-Start-Date-Latest")


class NeedFullLoad(RuntimeError):
    """The incremental sync needs a completed full load first."""


class SyncBusy(RuntimeError):
    """Another sync or refresh holds the lock."""


@contextmanager
def sync_lock(path: Path) -> Iterator[None]:
    """One index job at a time: a 15-min sync skips its turn while the nightly refresh runs, so the two
    never send requests to GeM side by side."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SyncBusy(str(path)) from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@dataclass
class FullLoadReport:
    pages: int = 0
    new: int = 0
    updated: int = 0
    num_found: int = 0
    complete: bool = False
    missed: int = 0
    gone: int = 0
    closed: int = 0


@dataclass
class SyncReport:
    pages: int = 0
    new: int = 0
    updated: int = 0
    closed: int = 0


async def full_load(
    index: BidIndex,
    session: BidplusSession,
    *,
    now: Callable[[], datetime] = site_now,
    max_pages: int | None = None,
    gone_after: int = 2,
) -> FullLoadReport:
    """Page the whole open-bids list newest first. Used for the first load and the nightly re-fetch
    (closing dates moved by corrigenda, bids that disappeared). Resumes from its checkpoint after a stop."""
    report = FullLoadReport()
    next_page = index.state(NEXT_PAGE)
    if next_page and index.state(FULL_STARTED):
        page_no, started = int(next_page), datetime.fromisoformat(index.state(FULL_STARTED))
        log.info("resuming full load at page %d (started %s)", page_no, started)
    else:
        page_no, started = 1, now()
        index.set_state(FULL_STARTED, started.isoformat(timespec="seconds"))
    while True:
        page = await list_all_bids(session, NEWEST_FIRST, page_no)
        counts = index.upsert(page.bids, seen_at=now())
        report.pages += 1
        report.new += counts.new
        report.updated += counts.updated
        report.num_found = max(report.num_found, page.num_found)
        index.set_state(NEXT_PAGE, str(page_no + 1))
        if page.empty_confirmed or page_no >= page.page_count:
            break
        if page_no % 100 == 0:
            log.info("full load: page %d of %d", page_no, page.page_count)
        page_no += 1
        if max_pages is not None and report.pages >= max_pages:
            return report
    report.complete = True
    index.set_state(NEXT_PAGE, None)
    index.set_state(NUM_FOUND, str(report.num_found))
    report.missed, report.gone = index.mark_missed(refresh_started=started, gone_after=gone_after)
    report.closed = index.close_expired(now=now())
    # Bids published after the scan began may sit above page 1 by now: the next sync starts from here.
    index.set_state(LAST_SYNC, started.isoformat(timespec="seconds"))
    return report


async def sync_recent(
    index: BidIndex,
    session: BidplusSession,
    *,
    now: Callable[[], datetime] = site_now,
    margin: timedelta = timedelta(minutes=30),
    max_pages: int = 2000,
) -> SyncReport:
    """Newest first, until a page's newest bid was published before the last sync (less a margin, as GeM takes
    up to 15 min to list a new bid): everything below it is older still. Known RAs dated in the future sit on the
    first pages, so "first known bid" would stop too early; and older pages hold bids the full load skipped
    (~2.7%, the list shifts while it is read), so "first all-known page" would never stop. Those skipped bids
    are the nightly re-fetch's job."""
    last = index.state(LAST_SYNC)
    if not last:
        raise NeedFullLoad("run the full load first")
    started = now()
    threshold = datetime.fromisoformat(last) - margin
    report = SyncReport()
    for page_no in range(1, max_pages + 1):
        page = await list_all_bids(session, NEWEST_FIRST, page_no)
        counts = index.upsert(page.bids, seen_at=now())
        report.pages += 1
        report.new += counts.new
        report.updated += counts.updated
        index.set_state(NUM_FOUND, str(page.num_found))
        if page.empty_confirmed or page_no >= page.page_count:
            break
        newest = max((b.published for b in page.bids if b.published), default=None)
        if newest is None or newest < threshold:
            break
    index.set_state(LAST_SYNC, started.isoformat(timespec="seconds"))
    report.closed = index.close_expired(now=now())
    return report


def embed_pending(index: BidIndex, embedder: Embedder, batch_size: int = 256) -> int:
    """Embed open bids that have no current embedding. Returns how many were embedded."""
    pending = index.pending_embeddings(embedder.model_name)
    for i in range(0, len(pending), batch_size):
        chunk = pending[i : i + batch_size]
        vectors = embedder.encode([text for _, text in chunk])
        index.store_embeddings(embedder.model_name, [(b, t, v) for (b, t), v in zip(chunk, vectors, strict=True)])
        if len(pending) > batch_size:
            log.info("embedded %d of %d", min(i + batch_size, len(pending)), len(pending))
    return len(pending)
