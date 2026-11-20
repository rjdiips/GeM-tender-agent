"""Lookup lists for the Planner (spec 5.2, R2): GeM's categories and ministries from the site, replaced daily
(spec 11.1), merged with the ministries and departments the bid cache knows: those its bids carry and those any
search's listing named (BidIndex.buyer_names).

GeM's department list (`dept-list-adv`) needs a ministry and an organisation per request, so the site's whole list
would take thousands of requests a day; departments come from the listings searches already read instead."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path

import numpy as np

from gem_agent.index.db import BidIndex, buyer_name
from gem_agent.retrieval.bm25 import lexical_scores
from gem_agent.retrieval.embed import Embedder
from gem_agent.retrieval.fusion import reciprocal_rank_fusion
from gem_agent.tools.gem.lookups import Category, list_categories, list_ministries
from gem_agent.tools.http_client import BidplusSession

_STOP = {"the", "and", "for", "from", "of", "only", "all", "any", "with"}


def _clean(names: list[str]) -> list[str]:
    """Each name normalised (buyer_name), unsafe ones dropped, first spelling kept."""
    return list(dict.fromkeys(c for n in names if (c := buyer_name(n))))


class LookupCatalog:
    def __init__(self, cache_dir: Path, bid_cache: BidIndex, embedder: Embedder | None = None) -> None:
        self.dir = Path(cache_dir) / "lookups"
        self.bid_cache, self.embedder = bid_cache, embedder
        self._categories: list[Category] | None = None
        self._vectors: np.ndarray | None = None

    # --- refresh from the site ----------------------------------------------------------------------------------

    def _read(self, name: str) -> dict | None:
        path = self.dir / f"{name}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def _write(self, name: str, data: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")

    def is_fresh(self, today: date) -> bool:
        data = self._read("categories")
        return bool(data) and data["date"] == today.isoformat()

    async def refresh(self, session: BidplusSession, *, today: date) -> None:
        cats = await list_categories(session)
        self._write("categories", {"date": today.isoformat(), "items": [c.model_dump() for c in cats]})
        self._write("ministries", {"date": today.isoformat(), "items": await list_ministries(session)})
        self._categories, self._vectors = cats, None
        if self.embedder is not None:
            self._category_vectors()

    # --- lookups ------------------------------------------------------------------------------------------------

    def categories(self) -> list[Category]:
        if self._categories is None:
            data = self._read("categories")
            self._categories = [Category(**c) for c in data["items"]] if data else []
        return self._categories

    def _category_vectors(self) -> np.ndarray | None:
        """Embeddings of the category labels, cached beside the list (keyed by model and label set)."""
        if self.embedder is None or not self.categories():
            return None
        if self._vectors is None:
            labels = [c.label for c in self.categories()]
            key = hashlib.sha1(("\n".join(labels) + self.embedder.model_name).encode()).hexdigest()[:12]
            path = self.dir / f"categories.{key}.npy"
            if path.exists():
                self._vectors = np.load(path)
            else:
                self._vectors = self.embedder.encode(labels).astype(np.float16)
                self.dir.mkdir(parents=True, exist_ok=True)
                np.save(path, self._vectors)
        return self._vectors.astype(np.float32)

    def find_categories(self, query: str, terms: list[str], k: int = 15) -> list[Category]:
        cats = self.categories()
        if not cats:
            return []
        rankings = []
        scores = lexical_scores([c.label for c in cats], terms)
        rankings.append([i for i in np.argsort(scores)[::-1] if scores[i] > 0][: k * 3])
        vectors = self._category_vectors()
        if vectors is not None and query:
            sims = vectors @ self.embedder.encode([query])[0]  # type: ignore[union-attr]
            rankings.append(list(np.argsort(sims)[::-1][: k * 3]))
        return [cats[int(i)] for i in list(reciprocal_rank_fusion(rankings))[:k]]

    def ministries(self, k: int = 100) -> list[str]:
        """Up to `k` ministry values, each normalised (index.db.buyer_name): the cached bids' values (most bids
        first), then the site's list, then names only searches listed. Every site ministry always keeps its slot;
        listing-only names fill what is left of `k`. The Planner can only choose a listed ministry; the bound keeps
        its prompt and enum small whatever the listings bring."""
        data = self._read("ministries")
        site = _clean([*(data["items"] if data else [])])
        ordered = _clean([*(m for m, _ in self.bid_cache.distinct("ministry")), *site,
                          *self.bid_cache.buyer_names("ministry")])
        reserved, spare = set(site), max(0, k - len(site))
        out: list[str] = []
        for value in ordered:
            if value in reserved:
                out.append(value)
            elif spare:
                out.append(value)
                spare -= 1
        return out[:k]

    def departments(self, hints: list[str], k: int = 30) -> list[str]:
        words = {w.lower() for h in hints for w in re.findall(r"\w+", h) if len(w) > 2 and w.lower() not in _STOP}
        if not words:
            return []
        words |= {w[:-1] for w in words if w.endswith("s") and len(w) > 4} | {w + "s" for w in words if not w.endswith("s")}
        counts: dict[str, int] = {}  # normalised name -> cached bids carrying it
        for name, n in self.bid_cache.distinct("department"):
            if clean := buyer_name(name):
                counts[clean] = counts.get(clean, 0) + n
        for name in self.bid_cache.buyer_names("department"):
            if clean := buyer_name(name):
                counts.setdefault(clean, 0)
        depts = list(counts.items())
        scores = lexical_scores([d for d, _ in depts], sorted(words))
        ranked = sorted((i for i, s in enumerate(scores) if s > 0), key=lambda i: (-scores[i], -depts[i][1]))
        return [depts[i][0] for i in ranked[:k]]
