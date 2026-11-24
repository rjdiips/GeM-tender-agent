"""probe_live: how a candidate meaning fares against GeM right now, from page 1 of a few searches."""
from __future__ import annotations

from gem_agent.agents.schemas import ProbeResult, ResolvedWindow, SearchPlan, TopicDefinition
from gem_agent.agents.searcher import Searcher
from gem_agent.tools.gem.live import LiveSearcher, SearchSpec
from gem_agent.tools.gem.models import Bid


async def probe_live(live: LiveSearcher, topic: TopicDefinition, window: ResolvedWindow, *, k: int = 8,
                     max_terms: int = 3) -> ProbeResult:
    terms = list(dict.fromkeys([*topic.match_terms, *topic.acronyms]))[:max_terms]
    outcomes = await live.first_pages([SearchSpec(kind="fullText", value=t) for t in terms])
    counted = [o for o in outcomes if not (o.progress.skipped or o.progress.same_as or o.progress.failed)]
    # The searcher's own scope check handles one-sided published windows and closing windows. Buyer filters and
    # bid types are empty at probe time: that plan belongs to the Planner, which runs after the probe.
    plan = SearchPlan(topic_key=topic.key(), text=topic.meaning, terms=[], **window.model_dump())
    bids: dict[int, Bid] = {}  # distinct page-1 bids in the window that a search matched; a bid two terms return counts once
    for o in counted:
        for bid in o.bids:
            if not Searcher._in_scope(bid, plan):
                continue
            bids.setdefault(bid.b_id, bid)
    return ProbeResult(meaning=topic.meaning, strong=len(bids),
                       top_titles=list(dict.fromkeys(b.title for b in bids.values()))[:k])
