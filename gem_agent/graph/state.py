"""RunState (Pydantic) carried through the graph and saved by the checkpointer after every node."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from gem_agent.agents.schemas import SearchPlan, TenderRequest
from gem_agent.agents.searcher import Candidate


class RunState(BaseModel):
    run_id: str
    user: str
    request: TenderRequest
    plans: list[SearchPlan] = []
    candidates: list[Candidate] = []
    pool: int = 0
    over_cap: bool = False
    adjust: Literal["", "widen", "narrow"] = ""
    retries: int = 0
    done_ids: list[int] = []  # bids already collected and judged (skipped when a run resumes)
