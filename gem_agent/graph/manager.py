"""Run manager: turns a chat message into a run, an answer, or a follow-on run, and streams the events.

- search: a new run (graph: plan → search → process → report).
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
from typing import Any

from gem_agent.graph.build import build_graph, emit
from gem_agent.graph.checkpoint import checkpointer
from gem_agent.graph.services import Services
from gem_agent.graph.state import RunState
from gem_agent.graph.store import RunRecord
from gem_agent.graph.views import run_list_item, run_view, understood_view, with_names

log = logging.getLogger(__name__)

class RunNotRunning(RuntimeError):
    """Stop was asked of a run that is not running (finished, stopped, or failed already)."""


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

    async def restore(self, user: str, run_id: str, b_id: int) -> None:
        """Move a quarantined bid back to relevant; recorded for the memory step (spec 5.5)."""
        self._own(user, run_id)
        outcomes = self.s.store.outcomes(run_id)
        outcome = outcomes[b_id]
        outcome.restored = True
        self.s.store.save_outcome(run_id, outcome)
        self.s.store.append_event(run_id, {"type": "signal", "kind": "restored", "b_id": b_id,
                                           "at": self.s.now().isoformat(timespec="seconds")})
