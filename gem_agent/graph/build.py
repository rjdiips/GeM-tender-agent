"""The run graph (spec 5.1): plan → search → (widen / narrow, at most 2 retries) → process → report.

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
from gem_agent.graph.services import Services
from gem_agent.graph.state import RunState
from gem_agent.graph.store import Outcome
from gem_agent.graph.views import bid_result

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


def build_graph(s: Services) -> StateGraph:
    async def plan(state: RunState) -> dict[str, Any]:
        note = WIDEN if state.adjust == "widen" else NARROW if state.adjust == "narrow" else ""
        plans = [await s.planner.plan(state.request, topic, note=note) for topic in state.request.topics]
        s.store.save_plans(state.run_id, plans)
        return {"plans": plans}

    async def search(state: RunState) -> dict[str, Any]:
        merged: dict[int, Candidate] = {}
        pool, over_cap = 0, False
        for topic, plan_ in zip(state.request.topics, state.plans, strict=True):
            result = await s.searcher.search(topic, plan_)
            pool += result.pool
            over_cap |= result.over_cap
            for c in result.candidates:
                merged.setdefault(c.bid.b_id, c)
        candidates = sorted(merged.values(), key=lambda c: c.bid.closing.isoformat() if c.bid.closing else "9")
        s.store.save_shortlist(state.run_id, candidates)
        if candidates:
            progress(s, state.run_id, f"Shortlist: {len(candidates)} bids from {pool:,} titles looked at "
                                      "(closing soonest first). Reading documents…")
        return {"candidates": candidates, "pool": pool, "over_cap": over_cap}

    def after_search(state: RunState) -> str:
        if state.retries < MAX_RETRIES and (not state.candidates or state.over_cap):
            return "retry"
        return "process"

    async def retry(state: RunState) -> dict[str, Any]:
        adjust = "narrow" if state.candidates else "widen"
        progress(s, state.run_id, "Nothing matched by title; widening the search…" if adjust == "widen"
                 else "Too many matches; narrowing the search…")
        return {"adjust": adjust, "retries": state.retries + 1}

    async def process(state: RunState) -> dict[str, Any]:
        topics = {short_name(t): t for t in state.request.topics}
        slots = asyncio.Semaphore(s.workers)
        done = list(state.done_ids)
        total = len(state.candidates)

        async def one(c: Candidate) -> None:
            async with slots:
                topic = topics.get(c.group) or state.request.topics[0]
                outcome = Outcome(b_id=c.bid.b_id, group=c.group, bid=c.bid)
                try:
                    files = await s.collector.collect(c.bid, attachments=False)
                    verdict, triage = await s.judge.screen(topic, c.bid, files)
                    if verdict is None:
                        files = await s.collector.collect(c.bid)
                        verdict = await s.judge.judge(topic, c.bid, files, triage=triage)
                    outcome.files, outcome.verdict, outcome.buyer_org = files.files, verdict, files.buyer_org
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
        return {"done_ids": done}

    async def report(state: RunState) -> dict[str, Any]:
        outcomes = list(s.store.outcomes(state.run_id).values())
        relevant = sum(1 for o in outcomes if o.verdict and o.verdict.relevant)
        quarantined = sum(1 for o in outcomes if o.verdict and not o.verdict.relevant)
        failed = sum(1 for o in outcomes if o.error)
        if not state.candidates:
            text = (f"No matching tenders found {state.request.window.describe()} "
                    f"(looked at {state.pool:,} titles, searched {state.retries + 1} ways).")
        else:
            text = f"Done: {relevant} relevant, {quarantined} quarantined" + (f", {failed} could not be read" if failed else "") + "."
        progress(s, state.run_id, text)
        return {}

    graph = StateGraph(RunState)
    for name, fn in (("plan", plan), ("search", search), ("retry", retry), ("process", process), ("report", report)):
        graph.add_node(name, fn)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "search")
    graph.add_conditional_edges("search", after_search, {"retry": "retry", "process": "process"})
    graph.add_edge("retry", "plan")
    graph.add_edge("process", "report")
    graph.add_edge("report", END)
    return graph
