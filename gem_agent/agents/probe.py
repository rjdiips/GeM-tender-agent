"""probe_live: how a candidate meaning fares against GeM right now, from page 1 of a few searches."""
from __future__ import annotations

import logging

from gem_agent.agents.schemas import ProbeResult, ResolvedWindow, TopicDefinition
from gem_agent.tools.gem.live import _READ_ERRORS, LiveSearcher, SearchSpec
from gem_agent.tools.gem.search import AllBidsQuery, list_all_bids
from gem_agent.tools.http_client import HostNotAllowed

log = logging.getLogger(__name__)


async def probe_live(live: LiveSearcher, topic: TopicDefinition, window: ResolvedWindow, *, k: int = 8,
                     max_terms: int = 3) -> ProbeResult:
    terms = list(dict.fromkeys([*topic.match_terms, *topic.acronyms]))[:max_terms]
    outcomes = await live.first_pages([SearchSpec(kind="fullText", value=t) for t in terms])
    counted = [o for o in outcomes if not (o.progress.skipped or o.progress.same_as or o.progress.failed)]
    strong = sum(o.progress.num_found for o in counted)
    titles: list[str] = []
    for o in counted:
        for bid in o.bids:
            if window.published_from and window.published_to and not (
                    bid.published and window.published_from <= bid.published <= window.published_to):
                continue
            titles.append(bid.title)
    try:
        window_count = (await list_all_bids(live.session, AllBidsQuery(), 1)).num_found
    except HostNotAllowed:
        raise
    except _READ_ERRORS as exc:
        log.warning("probe could not count open bids: %s", exc)
        window_count = 0
    return ProbeResult(meaning=topic.meaning, window_count=window_count, strong=strong,
                       top_titles=list(dict.fromkeys(titles))[:k])
