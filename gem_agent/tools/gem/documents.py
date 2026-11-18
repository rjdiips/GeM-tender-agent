"""Document routes by bid type, attachment links, downloads (spec 3.3)."""
from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Callable
from typing import Literal

import httpx
from pydantic import BaseModel

from gem_agent.tools.gem.models import Bid, BidType
from gem_agent.tools.http_client import BidplusSession, ClientPool
from gem_agent.tools.pdf.links import pdf_links

Kind = Literal["pdf", "html", "office", "archive", "empty", "other"]
LinkKind = Literal["document", "gtc", "parent_bid"]

_ARCHIVE_MAGIC = (b"PK\x03\x04", b"Rar!", b"7z\xbc\xaf\x27\x1c", b"\x1f\x8b")
_FILENAME_RE = re.compile(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', re.IGNORECASE)


def document_path(bid: Bid) -> str:
    """The bid's own document. An RA's showbidDocument is an empty PDF, so each type has its route."""
    if bid.bid_type == BidType.RA:
        return f"/list-ra-schedules/{bid.b_id}" if bid.eval_type > 0 else f"/showradocumentPdf/{bid.b_id}"
    if bid.bid_type == BidType.DIRECT_RA:
        return f"/showdirectradocumentPdf/{bid.b_id}"
    return f"/showbidDocument/{bid.b_id}"


def parent_document_path(bid: Bid) -> str | None:
    """For a reverse auction, the original bid's document."""
    return f"/showbidDocument/{bid.parent_b_id}" if bid.parent_b_id else None


class Download(BaseModel):
    url: str
    status: int
    content: bytes
    content_type: str | None
    filename: str
    kind: Kind

    @property
    def size(self) -> int:
        return len(self.content)


def classify(content: bytes) -> Kind:
    """By content, not by the server's content type (GeM labels empty bodies as PDF or HTML)."""
    if not content:
        return "empty"
    if content.startswith(b"%PDF"):
        return "pdf"
    if content.startswith(b"PK\x03\x04") and _is_ooxml(content):
        return "office"  # docx / xlsx are zip files with an OOXML manifest
    if content.startswith(_ARCHIVE_MAGIC):
        return "archive"
    head = content[:512].lstrip().lower()
    if head.startswith((b"<!doctype html", b"<html")):
        return "html"
    return "other"


def _is_ooxml(content: bytes) -> bool:
    try:
        return "[Content_Types].xml" in zipfile.ZipFile(io.BytesIO(content)).namelist()
    except zipfile.BadZipFile:
        return False


def _filename(resp: httpx.Response, url: str) -> str:
    match = _FILENAME_RE.search(resp.headers.get("content-disposition", ""))
    if match:
        return match.group(1).strip()
    segments = [s for s in httpx.URL(url).path.split("/") if s]
    return segments[-1] if segments else "download"


async def download(pool: ClientPool, url: str) -> Download:
    """GET any allowlisted URL through its host's client. Off-allowlist hosts raise HostNotAllowed."""
    resp = await pool.request("GET", url)
    return Download(url=url, status=resp.status_code, content=resp.content,
                    content_type=resp.headers.get("content-type"), filename=_filename(resp, url),
                    kind=classify(resp.content))


async def fetch_bid_document(session: BidplusSession, bid: Bid) -> Download:
    return await download(session.pool, session.url(document_path(bid)))


class AttachmentLink(BaseModel):
    url: str
    page: int
    kind: LinkKind


def _link_kind(url: str) -> LinkKind:
    u = httpx.URL(url)
    if u.host.startswith("admin.") and "/gtc/" in u.path:
        return "gtc"  # same GeM terms for every bid on a date: download once and cache
    if u.path.startswith("/showbidDocument/"):
        return "parent_bid"
    return "document"


def attachment_links(pdf: bytes, *, allowed: Callable[[str], bool]) -> list[AttachmentLink]:
    """Links in a bid PDF, kept only on allowed hosts, first occurrence of each URL."""
    seen: set[str] = set()
    links: list[AttachmentLink] = []
    for page, url in pdf_links(pdf):
        if not url.startswith(("http://", "https://")) or url in seen or not allowed(url):
            continue
        seen.add(url)
        links.append(AttachmentLink(url=url, page=page, kind=_link_kind(url)))
    return links
