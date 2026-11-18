"""Shared test helpers: a fake clock and a fake GeM site that serves responses captured live on 16 Nov 2026."""
from __future__ import annotations

import gzip
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "gem"


def fixture_bytes(name: str) -> bytes:
    """Body of a captured response; names ending in .gz are decompressed."""
    data = (FIXTURES / name).read_bytes()
    return gzip.decompress(data) if name.endswith(".gz") else data


def fixture_response(name: str) -> httpx.Response:
    """Rebuild a captured response with its real status and content type."""
    stem = name.removesuffix(".gz").rsplit(".", 1)[0]
    meta_path = FIXTURES / f"{stem}.meta.json"
    if not meta_path.exists():
        meta_path = FIXTURES / f"{name.removesuffix('.gz')}.meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {"status": 200, "content_type": None}
    headers = {"content-type": meta["content_type"]} if meta.get("content_type") else {}
    return httpx.Response(meta["status"], content=fixture_bytes(name), headers=headers)


class FakeClock:
    """Monotonic clock whose sleep() advances time instantly."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


Handler = Callable[[httpx.Request], httpx.Response]


@dataclass
class FakeGem:
    """Routes (method, host, path) to captured responses and records every request."""

    routes: dict[tuple[str, str, str], list[Handler | str]] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)

    def add(self, method: str, url: str, *responses: Handler | str) -> None:
        """Responses are served in order; the last one repeats."""
        u = httpx.URL(url)
        self.routes[(method, u.host, u.path)] = list(responses)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = (request.method, request.url.host, request.url.path)
        if key not in self.routes:
            return httpx.Response(599, text=f"no fake route for {key}")
        queue = self.routes[key]
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        return item(request) if callable(item) else fixture_response(item)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def form(self, i: int) -> dict[str, str]:
        """Decoded form body of the i-th recorded request."""
        return dict(httpx.QueryParams(self.requests[i].content.decode()))


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def gem() -> FakeGem:
    return FakeGem()


DOC_FIXTURES = Path(__file__).parent / "fixtures" / "docs"


def doc_bytes(name: str) -> bytes:
    return (DOC_FIXTURES / name).read_bytes()


def make_pdf(*pages: str) -> bytes:
    """A small text PDF, one string per page (an empty string gives a page with no text layer)."""
    import pymupdf

    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text, fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def make_pdf_with_links(text: str, urls: list[str]) -> bytes:
    """One text page carrying URI link annotations, like a GeM bid document."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text, fontsize=11)
    for i, url in enumerate(urls):
        rect = pymupdf.Rect(72, 100 + 20 * i, 300, 115 + 20 * i)
        page.insert_text((72, 112 + 20 * i), url[:60], fontsize=8)
        page.insert_link({"kind": pymupdf.LINK_URI, "from": rect, "uri": url})
    data = doc.tobytes()
    doc.close()
    return data
