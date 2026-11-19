"""RunState (Pydantic) carried through the graph and saved by the checkpointer after every node."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from gem_agent.agents.schemas import SearchPlan, TenderRequest
from gem_agent.agents.searcher import Candidate
from gem_agent.tools.gem.live import SearchProgress


class RunState(BaseModel):
    run_id: str
    user: str
    request: TenderRequest
    plans: list[SearchPlan] = []
    candidates: list[Candidate] = []
    pool: int = 0
    over_cap: bool = False
    first_counts: dict[str, int] = {}  # topic.key() -> candidates its stage 1 kept: stage 2 gets the rest of the cap
    cut: int = 0  # titles the cap left out, both stages: reported, since they were never read
    adjust: Literal["", "widen", "narrow"] = ""
    retries: int = 0
    done_ids: list[int] = []  # bids already collected and judged (skipped when a run resumes)
    # first: page 1 of every search is being judged; rest: the remaining pages' new bids; done: both judged (spec 6.2)
    stage: Literal["first", "rest", "done"] = "first"
    search_progress: dict[str, dict[str, SearchProgress]] = {}  # topic.key() -> search key -> pages read so far
