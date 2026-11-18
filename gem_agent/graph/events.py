"""Run events, in the shape the web chat reads (web/src/api/types.ts RunEvent): started, understood, progress,
result, done, plus message and show for answers that need no search. Every event of a run is appended to its
log.jsonl, so a page that reconnects replays them."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

Event = dict[str, Any]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[Event]]] = defaultdict(list)

    def subscribe(self, run_id: str) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers[run_id].append(queue)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue[Event]) -> None:
        if queue in self._subscribers.get(run_id, []):
            self._subscribers[run_id].remove(queue)

    def publish(self, run_id: str, event: Event) -> None:
        for queue in list(self._subscribers.get(run_id, [])):
            queue.put_nowait(event)
