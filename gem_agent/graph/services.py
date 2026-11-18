"""Everything a run needs, passed to the graph's nodes (real agents in the app, fakes in tests)."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from gem_agent.graph.events import EventBus
from gem_agent.graph.store import RunStore
from gem_agent.memory.user_memory import MemoryStore


@dataclass
class Services:
    chat: Any  # ChatAgent
    planner: Any  # Planner
    searcher: Any  # Searcher
    collector: Any  # Collector
    judge: Any  # Judge
    store: RunStore
    bus: EventBus
    memory: MemoryStore
    now: Callable[[], datetime]
    checkpoint_path: Path
    rulings: Any = None  # RulingsAgent; None leaves borderline bids for the user
    workers: int = 4  # bids collected and judged at a time (site traffic is limited per host anyway)
