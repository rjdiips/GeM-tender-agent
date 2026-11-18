"""runs/<run_id>/ (spec 11): run.json, plan.json, shortlist.json, outcomes.json, log.jsonl. Run records are the
audit trail and are never deleted automatically (spec 11.1)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, TypeAdapter

from gem_agent.agents.schemas import ProbeResult, SearchPlan, TenderRequest, Verdict
from gem_agent.agents.searcher import Candidate
from gem_agent.cache.docs import FileRecord
from gem_agent.tools.gem.models import Bid

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


class Outcome(BaseModel):
    b_id: int
    group: str
    bid: Bid
    files: list[FileRecord] = []
    buyer_org: str | None = None
    verdict: Verdict | None = None
    error: str = ""
    restored: bool = False


_OUTCOMES = TypeAdapter(dict[int, Outcome])


class RunStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _dir(self, run_id: str) -> Path:
        return self.root / run_id

    def _write(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text)
        tmp.replace(path)

    def create(self, record: RunRecord) -> None:
        self.save(record)

    def save(self, record: RunRecord) -> None:
        self._write(self._dir(record.run_id) / "run.json", record.model_dump_json(indent=1))

    def load(self, run_id: str) -> RunRecord:
        return RunRecord.model_validate_json((self._dir(run_id) / "run.json").read_text())

    def exists(self, run_id: str) -> bool:
        return (self._dir(run_id) / "run.json").exists()

    def list_runs(self, user: str) -> list[RunRecord]:
        records = [RunRecord.model_validate_json(p.read_text()) for p in self.root.glob("*/run.json")]
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

    def save_outcome(self, run_id: str, outcome: Outcome) -> None:
        all_ = self.outcomes(run_id)
        all_[outcome.b_id] = outcome
        self._write(self._dir(run_id) / "outcomes.json", _OUTCOMES.dump_json(all_, indent=1).decode())

    def append_event(self, run_id: str, event: dict[str, Any]) -> None:
        path = self._dir(run_id) / "log.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(event, default=str) + "\n")

    def events(self, run_id: str) -> list[dict[str, Any]]:
        path = self._dir(run_id) / "log.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
