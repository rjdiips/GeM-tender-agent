"""Verdicts keyed by (b_id, topic-definition hash, document hash): the same question about the same bid is never
judged twice."""
from __future__ import annotations

import uuid
from pathlib import Path

from gem_agent.agents.schemas import Verdict


class VerdictCache:
    def __init__(self, root: Path, version: str = "") -> None:
        self.root = Path(root) / "verdicts"
        self.version = version  # the model and prompt that decided: another version's verdicts are misses

    def _path(self, b_id: int, topic_key: str, doc_hash: str) -> Path:
        suffix = f"-{self.version}" if self.version else ""
        return self.root / str(b_id) / f"{topic_key}-{doc_hash}{suffix}.json"

    def get(self, b_id: int, topic_key: str, doc_hash: str) -> Verdict | None:
        try:
            return Verdict.model_validate_json(self._path(b_id, topic_key, doc_hash).read_bytes())
        except (OSError, ValueError):  # missing, or unreadable: a miss (pydantic's ValidationError is a ValueError)
            return None

    def put(self, verdict: Verdict) -> None:
        path = self._path(verdict.b_id, verdict.topic_key, verdict.doc_hash)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(verdict.model_dump_json(indent=1), encoding="utf-8")
        tmp.replace(path)
