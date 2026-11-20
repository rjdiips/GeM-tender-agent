"""Adaptive per-host limiter: 1 s floor, 4 s after real errors (R9, spec 3.2).

With `shared_path`, the limiter's state lives in `<shared_path>.json`, guarded by `<shared_path>.lock`, so every
process on the machine (index timers, chat app, live tests) shares one spacing per host. A process holds the
lock from its wait until its response is back, so requests to one host are never parallel, even across processes.
"""
from __future__ import annotations

import asyncio
import json
import statistics
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

from gem_agent.tools.locking import acquire, release


@dataclass
class _State:
    interval: float
    last_start: float | None = None
    last_trouble: float | None = None
    latencies: list[float] = field(default_factory=list)


class AdaptiveRateLimiter:
    """Spaces request starts to one host.

    Any real error, or a median latency over `spike_median` across recent requests, moves the host to the
    `slow` interval. After `recover_after` seconds without trouble it returns to `floor`.
    """

    def __init__(
        self,
        *,
        floor: float = 1.0,
        slow: float = 4.0,
        recover_after: float = 600.0,
        spike_median: float = 2.0,
        window: int = 5,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        shared_path: Path | None = None,
        lock_poll: float = 0.05,
    ) -> None:
        self.floor, self.slow = floor, slow
        self.recover_after, self.spike_median, self.window = recover_after, spike_median, window
        self._clock, self._sleep = clock, sleep
        self._shared = shared_path
        self._lock_poll = lock_poll
        self._state = _State(interval=floor)

    @property
    def interval(self) -> float:
        return self._state.interval

    @asynccontextmanager
    async def turn(self) -> AsyncIterator[None]:
        """Wait until this host may be called, then hold the host for the request."""
        async with self._file_lock():
            self._load()
            s = self._state
            if s.last_start is not None:
                delay = s.last_start + s.interval - self._clock()
                if delay > 0:
                    await self._sleep(delay)
            s.last_start = self._clock()
            try:
                yield
            finally:
                self._save()

    async def wait(self) -> None:
        async with self.turn():
            pass

    def record(self, *, ok: bool, latency: float) -> None:
        """Call inside turn() when shared, so the result reaches the other processes."""
        s = self._state
        s.latencies = [*s.latencies, latency][-self.window :]
        spike = len(s.latencies) >= 3 and statistics.median(s.latencies) > self.spike_median
        now = self._clock()
        if not ok or spike:
            s.interval, s.last_trouble = self.slow, now
        elif s.last_trouble is not None and now - s.last_trouble >= self.recover_after:
            s.interval, s.last_trouble = self.floor, None

    # --- shared state -------------------------------------------------------------------------------------------

    @asynccontextmanager
    async def _file_lock(self) -> AsyncIterator[None]:
        if self._shared is None:
            yield
            return
        lock_file = self._shared.with_name(self._shared.name + ".lock")
        handle = acquire(lock_file, blocking=False)
        while handle is None:
            await asyncio.sleep(self._lock_poll)  # real time: another process is mid-request
            handle = acquire(lock_file, blocking=False)
        try:
            yield
        finally:
            release(handle)

    def _state_file(self) -> Path:
        assert self._shared is not None
        return self._shared.with_name(self._shared.name + ".json")

    def _load(self) -> None:
        if self._shared is None:
            return
        try:
            data = json.loads(self._state_file().read_text(encoding="utf-8"))
            self._state = _State(**data)
        except (OSError, ValueError, TypeError):
            self._state = _State(interval=self.floor)

    def _save(self) -> None:
        if self._shared is None:
            return
        tmp = self._state_file().with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self._state)), encoding="utf-8")
        tmp.replace(self._state_file())
