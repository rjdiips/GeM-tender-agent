"""runs/<run_id>/: run.json, plan.json, shortlist.json, outcomes.json, log.jsonl. Run records are the
audit trail and are never deleted automatically."""
from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, TypeAdapter

from gem_agent.agents.schemas import ProbeResult, SearchPlan, TenderRequest, Verdict
from gem_agent.agents.searcher import Candidate
from gem_agent.cache.docs import FileRecord
from gem_agent.tools.gem.models import Bid
from gem_agent.tools.locking import locked

RunStatus = Literal["running", "complete", "failed", "stopped"]


class RunRecord(BaseModel):
    run_id: str
    user: str
    title: str
    request_text: str
    created: datetime
    status: RunStatus = "running"
    parent_run_id: str | None = None
    request: TenderRequest | None = None
    probes: list[ProbeResult] = []
    error: str = ""


class Ruling(BaseModel):
    """The reader's call on a borderline bid, kept on the outcome (never in the verdict cache)."""
    decision: Literal["counts", "not"]
    by: Literal["you", "earlier"]
    fact_id: str
    fact: str
    created_fact: bool = False
    at: datetime


class Outcome(BaseModel):
    b_id: int
    group: str
    bid: Bid
    files: list[FileRecord] = []
    buyer_org: str | None = None
    verdict: Verdict | None = None
    error: str = ""
    restored: bool = False
    ruling: Ruling | None = None
    undone: bool = False  # the user undid a ruling on this bid; never settle it automatically again


_OUTCOMES = TypeAdapter(dict[int, Outcome])


class RunStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _dir(self, run_id: str) -> Path:
        return self.root / run_id

    def _write(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)

    def create(self, record: RunRecord) -> None:
        self.save(record)

    def save(self, record: RunRecord) -> None:
        self._write(self._dir(record.run_id) / "run.json", record.model_dump_json(indent=1))

    def load(self, run_id: str) -> RunRecord:
        return RunRecord.model_validate_json((self._dir(run_id) / "run.json").read_text(encoding="utf-8"))

    def exists(self, run_id: str) -> bool:
        return (self._dir(run_id) / "run.json").exists()

    def list_runs(self, user: str) -> list[RunRecord]:
        records = [RunRecord.model_validate_json(p.read_text(encoding="utf-8")) for p in self.root.glob("*/run.json")]
        return sorted((r for r in records if r.user == user), key=lambda r: r.created, reverse=True)

    def save_plans(self, run_id: str, plans: list[SearchPlan]) -> None:
        self._write(self._dir(run_id) / "plan.json", json.dumps([p.model_dump(mode="json") for p in plans], indent=1))

    def save_shortlist(self, run_id: str, candidates: list[Candidate]) -> None:
        rows = [{"b_id": c.bid.b_id, "bid_number": c.bid.bid_number, "title": c.bid.title, "group": c.group, "why": c.why}
                for c in candidates]
        self._write(self._dir(run_id) / "shortlist.json", json.dumps(rows, indent=1))

    def outcomes(self, run_id: str) -> dict[int, Outcome]:
        path = self._dir(run_id) / "outcomes.json"
        return _OUTCOMES.validate_json(path.read_bytes()) if path.exists() else {}

    @contextmanager
    def _outcomes_lock(self, run_id: str) -> Iterator[None]:
        with locked(self._dir(run_id) / "outcomes.lock"):
            yield

    def _write_outcomes(self, run_id: str, all_: dict[int, Outcome]) -> None:
        self._write(self._dir(run_id) / "outcomes.json", _OUTCOMES.dump_json(all_, indent=1).decode())

    def save_outcome(self, run_id: str, outcome: Outcome) -> None:
        with self._outcomes_lock(run_id):
            all_ = self.outcomes(run_id)
            all_[outcome.b_id] = outcome
            self._write_outcomes(run_id, all_)

    def update_outcomes(self, run_id: str, fn: Callable[[dict[int, Outcome]], Iterable[int]]) -> dict[int, Outcome]:
        """Read-modify-write under the run's lock: `fn` edits the freshly read outcomes and returns the changed ids.
        Pure and synchronous, so no model call is ever made while the lock is held."""
        with self._outcomes_lock(run_id):
            all_ = self.outcomes(run_id)
            changed = list(fn(all_))
            self._write_outcomes(run_id, all_)
            return {b_id: all_[b_id] for b_id in changed}

    def append_event(self, run_id: str, event: dict[str, Any]) -> None:
        path = self._dir(run_id) / "log.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, default=str) + "\n")

    def events(self, run_id: str) -> list[dict[str, Any]]:
        path = self._dir(run_id) / "log.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
