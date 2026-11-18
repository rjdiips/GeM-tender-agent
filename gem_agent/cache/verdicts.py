"""Verdicts keyed by (b_id, topic-definition hash, document hash): the same question about the same bid is never
judged twice (spec 6.3)."""
from __future__ import annotations

from pathlib import Path

from gem_agent.agents.schemas import Verdict


class VerdictCache:
    def __init__(self, root: Path) -> None:
        self.root = Path(root) / "verdicts"

    def _path(self, b_id: int, topic_key: str, doc_hash: str) -> Path:
        return self.root / str(b_id) / f"{topic_key}-{doc_hash}.json"

    def get(self, b_id: int, topic_key: str, doc_hash: str) -> Verdict | None:
        path = self._path(b_id, topic_key, doc_hash)
        return Verdict.model_validate_json(path.read_bytes()) if path.exists() else None

    def put(self, verdict: Verdict) -> None:
        path = self._path(verdict.b_id, verdict.topic_key, verdict.doc_hash)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(verdict.model_dump_json(indent=1))
        tmp.replace(path)
