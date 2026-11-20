"""Saved topic meanings in knowledge/topics/ (spec 5.3): what a user meant by a request, so a later request with
the same text starts from it. A request can mean several topics ("LOS and AML tenders"); they are saved together.
A correction always replaces what was saved."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from gem_agent.agents.schemas import TopicDefinition


def normalise(text: str) -> str:
    return " ".join(text.lower().split())


class TopicStore:
    def __init__(self, knowledge_dir: Path) -> None:
        self.root = Path(knowledge_dir) / "topics"

    def _dir(self, user: str) -> Path:
        return self.root / re.sub(r"[^\w.-]", "_", user)

    def _path(self, user: str, text: str) -> Path:
        key = hashlib.sha1(normalise(text).encode()).hexdigest()[:16]
        return self._dir(user) / f"{key}.json"

    @staticmethod
    def _topics(data: dict) -> list[TopicDefinition]:
        raw = data["topics"] if "topics" in data else [data["topic"]]  # "topic": saved before 16 Nov
        return [TopicDefinition.model_validate(t) for t in raw]

    def get(self, user: str, text: str) -> list[TopicDefinition]:
        path = self._path(user, text)
        return self._topics(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else []

    def save(self, user: str, text: str, topics: list[TopicDefinition]) -> None:
        path = self._path(user, text)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"text": normalise(text), "topics": [t.model_dump() for t in topics]}, indent=1),
                        encoding="utf-8")

    def all(self, user: str, limit: int | None = None) -> list[tuple[str, TopicDefinition]]:
        """(request text, meaning) for the user's saved requests, the most recently saved first; `limit` counts
        requests, so the chat prompt stays bounded however long the user's history grows."""
        paths = sorted(self._dir(user).glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        out = []
        for path in paths[:limit]:
            data = json.loads(path.read_text(encoding="utf-8"))
            out.extend((data["text"], topic) for topic in self._topics(data))
        return out
