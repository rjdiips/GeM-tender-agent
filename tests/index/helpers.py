"""Test-only builders: listing rows cloned from a real GeM row, a fake newest-first listing, fake models."""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime

import httpx
import numpy as np

from tests.conftest import fixture_bytes

_REAL_ROW = json.loads(fixture_bytes("search_category_p1.json"))["response"]["response"]["docs"][0]


def row(b_id: int, published: datetime, closing: datetime, title: str = "Supply of chairs", *,
        bid_type: int = 1, ministry: str | None = "Ministry of Finance", department: str = "Department of X") -> dict:
    """A listing row in GeM's own shape (one-item lists, "Z" timestamps)."""
    doc = copy.deepcopy(_REAL_ROW)
    doc.update({
        "id": str(b_id), "b_id": [b_id], "b_bid_number": [f"GEM/2026/{'R' if bid_type == 2 else 'B'}/{b_id}"],
        "b_category_name": [title], "bd_category_name": [title], "b_bid_type": [bid_type],
        "final_start_date_sort": [published.strftime("%Y-%m-%dT%H:%M:%SZ")],
        "final_end_date_sort": [closing.strftime("%Y-%m-%dT%H:%M:%SZ")],
        "ba_official_details_deptName": [department],
    })
    if ministry:
        doc["ba_official_details_minName"] = [ministry]
    return doc


class FakeListing:
    """all-bids-data sorted newest-published first, 10 per page, "No data found" past the end."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.pages_served: list[int] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(dict(httpx.QueryParams(request.content.decode()))["payload"])
        page = payload["page"]
        self.pages_served.append(page)
        ordered = sorted(self.rows, key=lambda d: (d["final_start_date_sort"][0], d["b_id"][0]), reverse=True)
        start = (page - 1) * 10
        docs = ordered[start : start + 10]
        if not docs:
            return httpx.Response(404, text='{"status":0,"code":404,"message":"No data found"}')
        body = {"status": 1, "code": 200, "response": {"response": {"numFound": len(ordered), "start": start, "docs": docs}}}
        return httpx.Response(200, json=body)


class HashEmbedder:
    """Bag-of-words hashed into 256 dims: texts sharing words get similar vectors."""

    model_name = "test-hash"

    def __init__(self) -> None:
        self.calls: list[int] = []

    def encode(self, texts: list[str]) -> np.ndarray:
        self.calls.append(len(texts))
        out = np.zeros((len(texts), 256), dtype=np.float32)
        for i, text in enumerate(texts):
            for word in text.lower().split():
                out[i, int(hashlib.md5(word.encode()).hexdigest(), 16) % 256] += 1
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.where(norms == 0, 1, norms)


class FixedReranker:
    """Scores texts containing the preferred word highest."""

    def __init__(self, preferred: str) -> None:
        self.preferred = preferred.lower()
        self.seen: list[int] = []

    def score(self, query: str, texts: list[str]) -> list[float]:
        self.seen.append(len(texts))
        return [1.0 if self.preferred in t.lower() else 0.0 for t in texts]
