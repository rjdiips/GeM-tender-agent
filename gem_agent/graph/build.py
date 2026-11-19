"""The run graph (spec 5.1, 6.2): plan → search → (widen / narrow, at most 2 retries) → process → search_rest →
process → report.

Searching has two stages (spec 6.2): `search` reads page 1 of every search and `process` judges that shortlist, so the
first verdicts do not wait for a broad search; `search_rest` then reads the remaining pages, and the second `process`
judges only the bids not seen before. The checkpointed `stage` and `search_progress` let a resumed run carry on at
the second stage without reading page 1 again.

Understanding happens before the graph (the run manager needs the intent to decide whether there is a run at all).
`process` collects and judges each candidate, closing soonest first, a few at a time: the bid document first and
triage on it, the attachments only for bids that pass. Verdicts and documents are cached, so a resumed run skips
work already done; `done_ids` in the checkpointed state skips it without even a cache lookup.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from typing import Any

from langgraph.graph import END, START, StateGraph

from gem_agent.agents.chat import short_name
from gem_agent.agents.searcher import Candidate
from gem_agent.graph.rulings import settle, topic_for
from gem_agent.graph.services import Services
from gem_agent.graph.state import RunState
from gem_agent.graph.store import Outcome
from gem_agent.graph.views import bid_result, bid_state
from gem_agent.tools.gem.live import SearchProgress
from gem_agent.tools.gem.titles import is_vague_title

log = logging.getLogger(__name__)
MAX_RETRIES = 2
WIDEN = ("The last search found no matching titles. Suggest broader extra_terms (synonyms, the wider field) and any "
         "categories that could hold such tenders.")
NARROW = "The last search matched too many titles. Suggest more specific extra_terms; avoid generic words."


def emit(s: Services, run_id: str, event: dict[str, Any]) -> None:
    s.store.append_event(run_id, event)
    s.bus.publish(run_id, event)


def progress(s: Services, run_id: str, text: str, kind: str | None = None) -> None:
    """kind "count": the running count, which the chat shows as one line updated in place (views._progress)."""
    event: dict[str, Any] = {"at": s.now().isoformat(timespec="seconds"), "text": text}
    if kind:
        event["kind"] = kind
    emit(s, run_id, {"type": "progress", "event": event})


def running_count(read: int, total: int, outcomes: Iterable[Outcome]) -> str:
    """What reading has done so far, counted from the stored outcomes (nothing is estimated)."""
    files = placeholders = dropped = rejected = relevant = failed = 0
    for o in outcomes:
        files += sum(1 for f in o.files if f.status == "ok")
        placeholders += sum(1 for f in o.files if f.status == "placeholder")
        if o.error:
            failed += 1
        elif o.verdict and o.verdict.relevant:
            relevant += 1
        elif o.verdict and o.verdict.stage == "triage":
            dropped += 1
        elif o.verdict:
            rejected += 1  # documents read in full, then quarantined
    parts = [f"{files} file{'s' if files != 1 else ''} downloaded"]
    if placeholders:
        parts.append(f"{placeholders} placeholder{'s' if placeholders != 1 else ''} skipped")
    parts.append(f"triage dropped {dropped}")
    if rejected:
        parts.append(f"{rejected} quarantined after reading")
    parts.append(f"{relevant} relevant so far")
    if failed:
        parts.append(f"{failed} could not be read")
    return f"Read {read} of {total} bids: {', '.join(parts)}…"


def cut_line(n: int) -> str:
    return (f"{n} more matching bid{' was' if n == 1 else 's were'} not read; narrow the request to see them.")


Progress = dict[str, dict[str, SearchProgress]]  # topic.key() -> search key -> progress


def failed_line(failed: int, total: int) -> str:
    return f"{failed} of {total} GeM searches could not be read; results may be incomplete."


def failed_searches(after: Progress, before: Progress | None = None) -> tuple[int, int]:
    """(failed, read) over distinct searches, all topics. Stage 1 (no `before`): every search. Stage 2: the searches
    that still had pages, and those of them that failed now."""
    read: set[str] = set()
    failed: set[str] = set()
    for topic, searches in after.items():
        for key, p in searches.items():
            was = (before or {}).get(topic, {}).get(key)
            if before is not None and (was is None or not was.pending):
                continue
            read.add(key)
            if p.failed:
                failed.add(key)
    return len(failed), len(read)


def gem_failed(search_progress: Progress) -> bool:
    """Searches failed and none that worked returned any bids: an empty shortlist says nothing about the topic."""
    searches = [p for t in search_progress.values() for p in t.values()]
    return any(p.failed for p in searches) and not any(
        not p.failed and not p.skipped and p.num_found > 0 for p in searches)


def wants_retry(*, stage: str, retries: int, candidates: list[Candidate], over_cap: bool,
                search_progress: Progress) -> bool:
    """After stage 1: narrow when the shortlist is over the cap. Widen when it is empty and no search has pages left:
    page 1 alone is the newest-published bids, so an empty page-1 shortlist goes on to stage 2 instead. Nor when GeM
    could not be read: a wider search would fail the same way."""
    if stage != "first" or retries >= MAX_RETRIES:
        return False
    if over_cap:
        return True
    pending = any(p.pending for searches in search_progress.values() for p in searches.values())
    return not candidates and not pending and not gem_failed(search_progress)


def by_closing(candidates: Iterable[Candidate]) -> list[Candidate]:
    return sorted(candidates, key=lambda c: c.bid.closing.isoformat() if c.bid.closing else "9")


def build_graph(s: Services) -> StateGraph:
    async def plan(state: RunState) -> dict[str, Any]:
        note = WIDEN if state.adjust == "widen" else NARROW if state.adjust == "narrow" else ""
        plans = [await s.planner.plan(state.request, topic, note=note) for topic in state.request.topics]
        s.store.save_plans(state.run_id, plans)
        return {"plans": plans}

    async def search(state: RunState) -> dict[str, Any]:
        """Stage 1: page 1 of every search."""
        merged: dict[int, Candidate] = {}
        pool, over_cap, cut = 0, False, 0
        search_progress: dict[str, Any] = {}
        first_counts: dict[str, int] = {}
        for topic, plan_ in zip(state.request.topics, state.plans, strict=True):
            result = await s.searcher.search(topic, plan_, stage="first")
            pool += result.pool
            over_cap |= result.over_cap
            cut += result.cut
            search_progress[topic.key()] = result.progress
            first_counts[topic.key()] = len(result.candidates)
            for c in result.candidates:
                merged.setdefault(c.bid.b_id, c)
        candidates = by_closing(merged.values())
        s.store.save_shortlist(state.run_id, candidates)
        if candidates:
            progress(s, state.run_id, f"Shortlist: {len(candidates)} bids from {pool:,} titles looked at "
                                      "(closing soonest first). Reading documents…")
        failed, read = failed_searches(search_progress)
        if failed:
            progress(s, state.run_id, failed_line(failed, read))
        if cut and not wants_retry(stage=state.stage, retries=state.retries, candidates=candidates, over_cap=over_cap,
                                   search_progress=search_progress):
            progress(s, state.run_id, cut_line(cut))  # a narrowing retry comes next otherwise
        return {"candidates": candidates, "pool": pool, "over_cap": over_cap, "search_progress": search_progress,
                "first_counts": first_counts, "cut": cut}

    def after_search(state: RunState) -> str:
        if wants_retry(stage=state.stage, retries=state.retries, candidates=state.candidates, over_cap=state.over_cap,
                       search_progress=state.search_progress):
            return "retry"
        return "process"

    async def search_rest(state: RunState) -> dict[str, Any]:
        """Stage 2: the remaining pages of every search. Only bids not already in the run are added (and judged).
        The cap is per topic across both stages, so each topic's stage 2 gets what its stage 1 left of it."""
        seen = {c.bid.b_id for c in state.candidates}
        new: list[Candidate] = []
        pool, cut = state.pool, 0
        search_progress = dict(state.search_progress)
        for topic, plan_ in zip(state.request.topics, state.plans, strict=True):
            key = topic.key()
            if key not in search_progress:  # no stage 1 to continue (a state from before two stages)
                log.warning("run %s: no stage-1 progress for %s; not searching deeper", state.run_id, key)
                continue
            taken = state.first_counts.get(key)
            if taken is None:  # a checkpoint from before first_counts: its stage-1 bids, by group
                taken = sum(1 for c in state.candidates if c.group == short_name(topic))
            result = await s.searcher.search(topic, plan_, stage="rest", progress=search_progress[key],
                                             exclude=set(seen), cap=s.searcher.settings.cap - taken)
            pool += result.pool
            cut += result.cut
            search_progress[key] = result.progress
            for c in result.candidates:
                if c.bid.b_id not in seen:  # the searcher may return a stage-1 bid again; it is judged once
                    seen.add(c.bid.b_id)
                    new.append(c)
        candidates = [*state.candidates, *by_closing(new)]
        if new:
            s.store.save_shortlist(state.run_id, candidates)
            progress(s, state.run_id, f"Searching deeper: {len(new)} more bids to read…")
        if cut:
            progress(s, state.run_id, cut_line(cut))
        failed, read = failed_searches(search_progress, before=state.search_progress)
        if failed:
            progress(s, state.run_id, failed_line(failed, read))
        return {"candidates": candidates, "pool": pool, "search_progress": search_progress, "stage": "rest",
                "cut": state.cut + cut}

    def after_process(state: RunState) -> str:
        return "search_rest" if state.stage == "first" else "report"

    async def retry(state: RunState) -> dict[str, Any]:
        adjust = "narrow" if state.candidates else "widen"
        progress(s, state.run_id, "Nothing matched by title; widening the search…" if adjust == "widen"
                 else "Too many matches; narrowing the search…")
        return {"adjust": adjust, "retries": state.retries + 1}

    async def process(state: RunState) -> dict[str, Any]:
        slots = asyncio.Semaphore(s.workers)
        done = list(state.done_ids)
        total = len(state.candidates)

        async def one(c: Candidate) -> None:
            async with slots:
                topic = topic_for(state.request, c.group)
                outcome = Outcome(b_id=c.bid.b_id, group=c.group, bid=c.bid)
                try:
                    files = await s.collector.collect(c.bid, attachments=False)
                    verdict, triage = await s.judge.screen(topic, c.bid, files)
                    if verdict is None:
                        files = await s.collector.collect(c.bid)
                        verdict = await s.judge.judge(topic, c.bid, files, triage=triage)
                    outcome.files, outcome.verdict, outcome.buyer_org = files.files, verdict, files.buyer_org
                    outcome.ruling = await settle(s, state.user, topic, verdict)
                except Exception as exc:  # noqa: BLE001 - one bid failing never stops the run (spec 7.1 rung 4)
                    log.warning("bid %s failed: %s", c.bid.bid_number, exc)
                    outcome.error = str(exc)[:300]
                s.store.save_outcome(state.run_id, outcome)
                done.append(c.bid.b_id)
                emit(s, state.run_id, {"type": "result", "bid": bid_result(state.run_id, outcome)})
                count_line(len(done))

        def count_line(read: int) -> None:
            """After every bid, the last one included: the chat shows only the newest count, so lines don't pile up."""
            outcomes = s.store.outcomes(state.run_id)
            progress(s, state.run_id, running_count(read, total, (outcomes[b] for b in done if b in outcomes)), "count")

        todo = [c for c in state.candidates if c.bid.b_id not in state.done_ids]
        if todo:
            count_line(len(done))  # at once, before the first bid finishes
        await asyncio.gather(*(one(c) for c in todo))
        return {"done_ids": done, **({"stage": "done"} if state.stage == "rest" else {})}

    async def report(state: RunState) -> dict[str, Any]:
        outcomes = list(s.store.outcomes(state.run_id).values())
        states = [bid_state(o) for o in outcomes if o.verdict]
        relevant, quarantined, borderline = (states.count(k) for k in ("relevant", "quarantined", "borderline"))
        failed = sum(1 for o in outcomes if o.error)
        searches = [p for t in state.search_progress.values() for p in t.values()]
        if not state.candidates and searches and all(p.failed for p in searches):
            total = len({k for t in state.search_progress.values() for k in t})
            text = f"GeM could not be searched: all {total} searches failed. Try again in a few minutes."
        elif not state.candidates:
            text = (f"No matching tenders found {state.request.window.describe()} "
                    f"(looked at {state.pool:,} titles, searched {state.retries + 1} ways).")
        else:
            text = f"Done: {relevant} relevant, {quarantined} quarantined" + (f", {failed} could not be read" if failed else "") + "."
            text += (" 1 bid needs your call: see Borderline." if borderline == 1
                     else f" {borderline} bids need your call: see Borderline." if borderline else "")
            if state.cut:
                text += " " + cut_line(state.cut)
        progress(s, state.run_id, text)
        await write_cards(state.run_id, [o.bid for o in outcomes if o.files and is_vague_title(o.bid.title)])
        return {}

    async def write_cards(run_id: str, bids: list[Any]) -> None:
        """Cards for the vague-title bids this run collected (spec 6.2). A failure is logged; the run is still done."""
        if s.cards is None or not bids:
            return
        try:
            await s.cards(bids)
        except Exception:  # cards help later searches; they never fail the run
            log.warning("run %s: writing cards for %d bids failed", run_id, len(bids), exc_info=True)

    graph = StateGraph(RunState)
    for name, fn in (("plan", plan), ("search", search), ("retry", retry), ("process", process),
                     ("search_rest", search_rest), ("report", report)):
        graph.add_node(name, fn)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "search")
    graph.add_conditional_edges("search", after_search, {"retry": "retry", "process": "process"})
    graph.add_edge("retry", "plan")
    graph.add_conditional_edges("process", after_process, {"search_rest": "search_rest", "report": "report"})
    graph.add_edge("search_rest", "process")
    graph.add_edge("report", END)
    return graph
