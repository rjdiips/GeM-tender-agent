"""Run manager: turns a chat message into a run, an answer, or a follow-on run, and streams the events.

- search: a new run (graph: plan → search → process → search_rest → process → report).
- refine: a follow-on run carrying the earlier requests as history; cached documents and verdicts are reused.
- show_rejected / memory_list / memory_forget / other: an answer on the current run's stream, no new run.
Runs execute as background tasks, so a closed page does not stop them; the stream can be rejoined (events()).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any, Literal

from gem_agent.agents.chat import short_name
from gem_agent.graph.build import build_graph, emit
from gem_agent.graph.checkpoint import checkpointer
from gem_agent.graph.rulings import settle, topic_for
from gem_agent.graph.services import Services
from gem_agent.graph.state import RunState
from gem_agent.graph.store import Outcome, Ruling, RunRecord
from gem_agent.graph.views import bid_result, run_list_item, run_view, understood_view, with_names

log = logging.getLogger(__name__)

class RunNotRunning(RuntimeError):
    """Stop was asked of a run that is not running (finished, stopped, or failed already)."""


class NotBorderline(RuntimeError):
    """A ruling was asked of a bid that is not waiting for one."""


class RulingFailed(RuntimeError):
    """The model could not write the fact; nothing was changed."""


ANSWERS = {
    "show_rejected": ("quarantine", "The quarantined bids are in the Quarantine tab, each with the reason. Restore any with one click."),
    "memory_list": ("memory", "Here is what I remember about you, with the evidence for each. Edit or forget any of them."),
    "memory_forget": ("memory", "Pick the fact to forget in the Memory tab."),
    "other": (None, "I find GeM tenders. Tell me what you are looking for, e.g. “AML software tenders published in the last 2 weeks”."),
}


class RunManager:
    def __init__(self, services: Services) -> None:
        self.s = services
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._rule_locks: dict[str, asyncio.Lock] = {}

    # --- asking ---------------------------------------------------------------------------------------------------

    async def ask(self, user: str, text: str, *, run_id: str | None = None) -> AsyncIterator[dict[str, Any]]:
        parent = self._own(user, run_id) if run_id else None
        history = self._history(parent) if parent else []
        new_id = uuid.uuid4().hex[:12]
        understanding = await self.s.chat.understand(user, text, history, run_id=new_id)
        request = understanding.request

        if request.intent not in ("search", "refine") or not request.topics:
            tab, message = ANSWERS.get(request.intent, ANSWERS["other"])
            events = ([{"type": "show", "tab": tab}] if tab else []) + [{"type": "message", "text": message}]
            for event in events:
                if parent:
                    emit(self.s, parent.run_id, event)
                yield event
            return

        record = RunRecord(run_id=new_id, user=user, title=text[:80], request_text=text, created=self.s.now(),
                           parent_run_id=parent.run_id if parent and request.intent == "refine" else None,
                           request=request, probes=understanding.probes)
        self.s.store.create(record)
        queue = self.s.bus.subscribe(new_id)  # every event, these first three included, arrives through it
        try:
            emit(self.s, new_id, {"type": "started", "run": run_view(record, {}, [])})
            understood = understood_view(record)
            if understood:
                emit(self.s, new_id, {"type": "understood", "understood": understood})
            elif understanding.progress_line:  # the understood card states the meaning; this is the fallback
                emit(self.s, new_id, {"type": "progress", "event": {
                    "at": self.s.now().isoformat(timespec="seconds"), "text": understanding.progress_line}})
            self._tasks[new_id] = asyncio.create_task(self._run(record))
            while True:
                event = await queue.get()
                yield event
                if event["type"] == "done":
                    break
        finally:
            self.s.bus.unsubscribe(new_id, queue)

    async def _run(self, record: RunRecord, *, resume: bool = False) -> None:
        config = {"configurable": {"thread_id": record.run_id}}
        status = "complete"
        try:
            async with checkpointer(self.s.checkpoint_path) as saver:
                graph = build_graph(self.s).compile(checkpointer=saver)
                start = None if resume else RunState(run_id=record.run_id, user=record.user, request=record.request)
                await graph.ainvoke(start, config)
        except asyncio.CancelledError:
            # Stopped from the web chat: bids already judged stay in the run; the checkpoint stays usable.
            status = "stopped"
            log.info("run %s stopped by the user", record.run_id)
        except Exception as exc:
            log.exception("run %s failed", record.run_id)
            status = "failed"
            record.error = str(exc)[:500]
        finally:
            record.status = status  # type: ignore[assignment]
            self.s.store.save(record)
            emit(self.s, record.run_id, {"type": "done", "status": status})

    async def stop(self, user: str, run_id: str) -> None:
        """Stop a running run now. Whatever was collected and judged so far stays in the run."""
        record = self._own(user, run_id)
        if record.status != "running":
            raise RunNotRunning(run_id)
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            return
        # Running, but not in this process (e.g. left over from a restart): mark it stopped.
        record.status = "stopped"
        self.s.store.save(record)
        emit(self.s, run_id, {"type": "done", "status": "stopped"})

    async def resume(self, run_id: str) -> None:
        """Continue a run that was interrupted (crash, restart) from its last checkpoint."""
        record = self.s.store.load(run_id)
        record.status = "running"
        self.s.store.save(record)
        await self._run(record, resume=True)

    def running_runs(self) -> list[str]:
        return [r.run_id for p in self.s.store.root.glob("*/run.json")
                if (r := RunRecord.model_validate_json(p.read_text())).status == "running" and r.run_id not in self._tasks]

    # --- reading --------------------------------------------------------------------------------------------------

    def _own(self, user: str, run_id: str) -> RunRecord:
        if not self.s.store.exists(run_id):
            raise KeyError(run_id)
        record = self.s.store.load(run_id)
        if record.user != user:
            raise PermissionError(run_id)
        return record

    def _history(self, record: RunRecord) -> list[str]:
        chain, current = [], record
        while current:
            chain.append(current.request_text)
            current = self.s.store.load(current.parent_run_id) if current.parent_run_id else None
        return list(reversed(chain))

    def view(self, user: str, run_id: str) -> dict[str, Any]:
        record = self._own(user, run_id)
        outcomes = with_names(self.s.store.outcomes(run_id), getattr(self.s.collector, "cache", None))
        return run_view(record, outcomes, self.s.store.events(run_id))

    def list_runs(self, user: str) -> list[dict[str, Any]]:
        return [run_list_item(r, self.s.store.outcomes(r.run_id)) for r in self.s.store.list_runs(user)]

    async def events(self, user: str, run_id: str) -> AsyncIterator[dict[str, Any]]:
        """Replay a run's events, then follow it live until it is done."""
        record = self._own(user, run_id)
        queue = self.s.bus.subscribe(run_id)
        try:
            for event in self.s.store.events(run_id):
                yield event
            if record.status != "running":
                return
            while True:
                event = await queue.get()
                yield event
                if event["type"] == "done":
                    return
        finally:
            self.s.bus.unsubscribe(run_id, queue)

    # --- actions --------------------------------------------------------------------------------------------------

    def _ruling_lock(self, run_id: str) -> asyncio.Lock:
        """One rule / undo / restore at a time per run, so a double click cannot write two facts.
        Model calls may run under it; the store's outcomes lock is a different (file) lock."""
        return self._rule_locks.setdefault(run_id, asyncio.Lock())

    async def restore(self, user: str, run_id: str, b_id: int) -> list[dict[str, Any]]:
        """Move a quarantined bid back to relevant; recorded for the memory step (spec 5.5).
        A bid the user ruled "doesn't count" is re-ruled "counts" instead, so the fact follows the click."""
        self._own(user, run_id)
        async with self._ruling_lock(run_id):
            outcome = self.s.store.outcomes(run_id)[b_id]
            if outcome.ruling and outcome.ruling.decision == "not":
                return await self._rule(user, run_id, b_id, "counts")

            def mark(all_: dict[int, Outcome]) -> list[int]:
                all_[b_id] = all_[b_id].model_copy(update={"restored": True})
                return [b_id]

            changed = self.s.store.update_outcomes(run_id, mark)
            self._signal(run_id, "restored", b_id)
            return [bid_result(run_id, o) for o in changed.values()]

    async def rule(self, user: str, run_id: str, b_id: int, decision: Literal["counts", "not"]) -> list[dict[str, Any]]:
        """The user's call on a borderline bid: it becomes a fact, and other bids it answers are settled by it."""
        self._own(user, run_id)
        async with self._ruling_lock(run_id):
            return await self._rule(user, run_id, b_id, decision)

    async def _rule(self, user: str, run_id: str, b_id: int, decision: Literal["counts", "not"]) -> list[dict[str, Any]]:
        """Every model call is made before the one write, so none runs while the outcomes lock is held."""
        s = self.s
        record = self._own(user, run_id)
        o = s.store.outcomes(run_id)[b_id]
        if not (o.verdict and o.verdict.borderline):
            raise NotBorderline(f"bid {b_id} is not borderline")
        if s.rulings is None:
            raise RulingFailed("rulings are not available")
        old = o.ruling if o.ruling and o.ruling.by == "you" else None
        if old and old.decision == decision:
            return []
        v, today = o.verdict, s.now().date()
        topic = topic_for(record.request, o.group)
        # The fact behind the bid's current ruling says the opposite: newest wins, so it is replaced whoever made it.
        contradicted = o.ruling.fact_id if o.ruling and o.ruling.decision != decision else None
        existing = [f for f in s.memory.ruling_facts(user, short_name(topic)) if f.id != contradicted]
        replaced: set[str] = {contradicted} if contradicted else set()
        why = f"replaced by your ruling on {o.bid.bid_number} on {today:%d %b}"
        try:
            match = await s.rulings.resolve(topic, v.question, v.reason, existing)
        except Exception as exc:
            raise RulingFailed(str(exc)) from exc
        matched = next((f for f in existing if match and f.id == match.fact_id), None)
        if match and matched and match.decision == decision:
            ruling = Ruling(decision=decision, by="you", fact_id=matched.id, fact=matched.fact, created_fact=False, at=s.now())
            chosen = matched
        else:
            try:
                text = (await s.rulings.write_fact(topic, v.question, decision)).strip()
            except Exception as exc:
                raise RulingFailed(str(exc)) from exc
            if not text:
                raise RulingFailed("the model wrote an empty fact")
            chosen = s.memory.add_ruling(user, text, topic=short_name(topic), run_id=run_id, today=today,
                                         evidence=f"you ruled on {o.bid.bid_number} on {today:%d %b}")
            ruling = Ruling(decision=decision, by="you", fact_id=chosen.id, fact=chosen.fact, created_fact=True, at=s.now())
            if match and matched:  # the old fact said the opposite
                replaced.add(matched.id)

        def stale(x: Outcome) -> bool:  # settled earlier by a fact that was just replaced
            return bool(x.ruling and x.ruling.by == "earlier" and x.ruling.fact_id in replaced)

        others = [x for x in s.store.outcomes(run_id).values()
                  if x.b_id != b_id and x.group == o.group and x.verdict and x.verdict.borderline
                  and ((x.ruling is None and not x.undone) or stale(x))]
        settled = {x.b_id: await settle(s, user, topic, x.verdict, facts=[chosen]) for x in others}
        moved = False

        def write(all_: dict[int, Outcome]) -> list[int]:
            nonlocal moved
            if all_[b_id].ruling != o.ruling:  # ruled or undone meanwhile by another writer: leave it be
                moved = True
                return [b_id]
            all_[b_id] = all_[b_id].model_copy(update={"ruling": ruling, "undone": False})
            changed = [b_id]
            for other_id, x in list(all_.items()):
                if other_id != b_id and stale(x):  # re-checked now: the replaced fact no longer stands
                    all_[other_id] = x.model_copy(update={"ruling": None})
                    changed.append(other_id)
            for other_id, earlier in settled.items():
                if earlier and other_id in all_ and _open(all_[other_id]):  # still waiting for the user
                    all_[other_id] = all_[other_id].model_copy(update={"ruling": earlier})
                    if other_id not in changed:
                        changed.append(other_id)
            return changed

        changed = s.store.update_outcomes(run_id, write)
        if moved:
            if ruling.created_fact:
                s.memory.forget(user, ruling.fact_id)
            return [bid_result(run_id, x) for x in changed.values()]
        for fact_id in replaced:
            s.memory.forget(user, fact_id, evidence=why)
        self._signal(run_id, "ruled", b_id, decision=decision)
        return [bid_result(run_id, x) for x in changed.values()]

    async def unrule(self, user: str, run_id: str, b_id: int) -> list[dict[str, Any]]:
        """Take a ruling back. A fact this click created goes with it, and so do the bids it settled."""
        self._own(user, run_id)
        async with self._ruling_lock(run_id):
            ruling = self.s.store.outcomes(run_id)[b_id].ruling
            if ruling is None:
                return []
            own_fact = ruling.by == "you" and ruling.created_fact
            moved = False

            def clear(all_: dict[int, Outcome]) -> list[int]:
                nonlocal moved
                if all_[b_id].ruling != ruling:  # changed meanwhile by another writer: leave it be
                    moved = True
                    return [b_id]
                changed = [b_id]
                if own_fact:
                    for other_id, x in all_.items():
                        if other_id != b_id and x.ruling and x.ruling.by == "earlier" and x.ruling.fact_id == ruling.fact_id:
                            all_[other_id] = x.model_copy(update={"ruling": None})
                            changed.append(other_id)
                all_[b_id] = all_[b_id].model_copy(update={"ruling": None, "undone": True})
                return changed

            changed = self.s.store.update_outcomes(run_id, clear)
            if not moved:
                if own_fact:
                    self.s.memory.forget(user, ruling.fact_id)
                self._signal(run_id, "unruled", b_id)
            return [bid_result(run_id, x) for x in changed.values()]

    def _signal(self, run_id: str, kind: str, b_id: int, **extra: Any) -> None:
        self.s.store.append_event(run_id, {"type": "signal", "kind": kind, "b_id": b_id, **extra,
                                           "at": self.s.now().isoformat(timespec="seconds")})


def _open(o: Outcome) -> bool:
    """A borderline bid nobody has ruled on, and that the user has not taken back."""
    return bool(o.verdict and o.verdict.borderline) and o.ruling is None and not o.undone
