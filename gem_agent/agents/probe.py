"""probe_live: how a candidate meaning fares against GeM right now, from page 1 of a few searches."""
from __future__ import annotations

from gem_agent.agents.schemas import ProbeResult, ResolvedWindow, TopicDefinition
from gem_agent.tools.gem.live import LiveSearcher, SearchSpec
from gem_agent.tools.gem.models import Bid


async def probe_live(live: LiveSearcher, topic: TopicDefinition, window: ResolvedWindow, *, k: int = 8,
                     max_terms: int = 3) -> ProbeResult:
    terms = list(dict.fromkeys([*topic.match_terms, *topic.acronyms]))[:max_terms]
    outcomes = await live.first_pages([SearchSpec(kind="fullText", value=t) for t in terms])
    counted = [o for o in outcomes if not (o.progress.skipped or o.progress.same_as or o.progress.failed)]
    bids: dict[int, Bid] = {}  # distinct page-1 bids in the window that a search matched; a bid two terms return counts once
    for o in counted:
        for bid in o.bids:
            if window.published_from and window.published_to and not (
                    bid.published and window.published_from <= bid.published <= window.published_to):
                continue
            bids.setdefault(bid.b_id, bid)
    return ProbeResult(meaning=topic.meaning, strong=len(bids),
                       top_titles=list(dict.fromkeys(b.title for b in bids.values()))[:k])
