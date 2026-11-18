"""Base searches kept for an hour, so a second run does not read the same pages again (spec 6.2)."""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from gem_agent.tools.gem.models import site_now


class SearchCache:
    def __init__(self, root: Path, *, ttl: timedelta = timedelta(hours=1),
                 now: Callable[[], datetime] = site_now) -> None:
        self.root, self.ttl, self.now = Path(root), ttl, now

    def _path(self, key: str) -> Path:
        return self.root / f"{hashlib.sha1(key.encode()).hexdigest()[:16]}.json"

    def get(self, key: str) -> tuple[int, list[dict]] | None:
        try:
            data = json.loads(self._path(key).read_text())
            if data["key"] != key or self.now() - datetime.fromisoformat(data["at"]) > self.ttl:
                return None
            return int(data["num_found"]), list(data["docs"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def put(self, key: str, num_found: int, docs: list[dict]) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"key": key, "at": self.now().isoformat(), "num_found": num_found, "docs": docs}))
        os.replace(tmp, path)
