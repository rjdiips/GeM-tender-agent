"""Everything a run needs, passed to the graph's nodes."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from gem_agent.graph.events import EventBus
from gem_agent.graph.store import RunStore
from gem_agent.memory.user_memory import MemoryStore
from gem_agent.tools.gem.models import Bid


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
    # Writes cards for the vague-title bids a run collected, after the run; None writes none
    cards: Callable[[list[Bid]], Awaitable[Any]] | None = None
    workers: int = 4  # bids collected and judged at a time (site traffic is limited per host anyway)
