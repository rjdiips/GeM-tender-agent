"""A fake GeM that answers searches from canned rows keyed by "kind|value"."""
from __future__ import annotations

import json

import httpx


class FakeSearchSite:
    """Handler for FakeGem.add: 10 rows per page, "No data found" past the end or for an unknown query."""

    def __init__(self, by_query: dict[str, list[dict]]) -> None:
        self.by_query = by_query
        self.served: list[tuple[str, int]] = []  # (key, page)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(dict(httpx.QueryParams(request.content.decode()))["payload"])
        page = payload["page"]
        if request.url.path.endswith("all-bids-data"):
            key = f"{payload['param']['searchType']}|{payload['param']['searchBid']}"
        else:
            assert payload["searchType"] == "bidNumber"
            key = f"category|{payload['category']}"
        self.served.append((key, page))
        rows = self.by_query.get(key, [])
        start = (page - 1) * 10
        docs = rows[start : start + 10]
        if not docs:
            return httpx.Response(404, text='{"status":0,"code":404,"message":"No data found"}')
        body = {"status": 1, "code": 200, "response": {"response": {"numFound": len(rows), "start": start, "docs": docs}}}
        return httpx.Response(200, json=body)
