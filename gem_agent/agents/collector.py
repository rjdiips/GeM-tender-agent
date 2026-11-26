"""Collector: for one bid, the bid PDF, every attachment it links to, the corrigendum, and text.

No model is needed: this is plain code, run per bid. Files come from the document cache when the bid has not
changed; a bid is fetched again only when a corrigendum appears or gains an entry.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from gem_agent.cache.docs import BidFiles, DocCache, FileRecord, Role
from gem_agent.cache.names import name_files
from gem_agent.tools.archive import ArchiveError, safe_members
from gem_agent.tools.extract import extract_text, suffix_for
from gem_agent.tools.gem.corrigendum import Corrigendum, other_details, view_corrigendum
from gem_agent.tools.gem.documents import (
    AttachmentLink,
    attachment_links,
    classify,
    document_path,
    download,
    parent_document_path,
)
from gem_agent.tools.gem.models import Bid, site_now
from gem_agent.tools.http_client import BidplusSession, GemHttpError, HostNotAllowed
from gem_agent.tools.pdf.ocr import ocr_page
from gem_agent.tools.pdf.placeholders import is_placeholder
from gem_agent.tools.pdf.text import OcrFn

log = logging.getLogger(__name__)


def _same_corrigendum(a: Corrigendum | None, b: Corrigendum | None) -> bool:
    return (a.entries if a else None) == (b.entries if b else None)


class DocumentUnavailable(RuntimeError):
    """The bid document itself could not be downloaded, so the bid cannot be read."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"bid document could not be downloaded: {reason}")


class Collector:
    def __init__(self, session: BidplusSession, cache: DocCache, ocr: OcrFn | None = ocr_page) -> None:
        self.session, self.cache, self.ocr = session, cache, ocr
        self._gtc_locks: dict[str, asyncio.Lock] = {}

    async def collect(self, bid: Bid, *, force: bool = False, attachments: bool = True) -> BidFiles:
        """attachments=False fetches only the bid document (and an RA's original bid): enough for triage, at 1-2
        requests. A later full collect reuses those and fetches the attachments."""
        cached = self.cache.load(bid.b_id)
        try:
            details = await other_details(self.session, bid.b_id)
            has_corrigendum = details.corrigendum
            corrigendum = await view_corrigendum(self.session, bid.b_id) if has_corrigendum else None
        except GemHttpError as exc:
            # The corrigendum check only decides whether cached files are still current; failing it must not
            # cost the bid. Keep what the cache knows (or "none") and read the documents anyway.
            log.warning("corrigendum check failed for %s, reading the bid without it: %s", bid.bid_number, exc)
            has_corrigendum = cached.corrigendum_flag if cached else False
            corrigendum = cached.corrigendum if cached else None
        current = bool(cached and not force and cached.main_ok and cached.corrigendum_flag == has_corrigendum
                       and _same_corrigendum(cached.corrigendum, corrigendum))
        fresh = current and cached is not None and (not attachments or cached.complete_ok)
        if fresh and cached and (not cached.partial or not attachments):
            changed = False
            if cached.buyer_org is None:  # manifests written before the field existed
                cached.buyer_org = self._buyer_org(cached.files)
                changed = bool(cached.buyer_org)
            changed = name_files(cached, self.cache) or changed  # likewise for file names
            if changed:
                self.cache.save(cached)
            return cached
        reuse = cached if current else None
        records = await self._fetch_all(bid, attachments=attachments, reuse=reuse)
        files = BidFiles(b_id=bid.b_id, bid_number=bid.bid_number, fetched_at=site_now(),
                         corrigendum_flag=has_corrigendum, corrigendum=corrigendum, files=records,
                         partial=not attachments, buyer_org=self._buyer_org(records))
        name_files(files, self.cache)
        self.cache.save(files)
        return files

    def _buyer_org(self, records: list[FileRecord]) -> str | None:
        from gem_agent.tools.gem.bid_fields import buyer_org

        doc = next((f for f in records if f.role == "bid_document" and f.status == "ok"), None)
        pages = self.cache.load_text(doc) if doc else []
        return buyer_org(pages[0].text) if pages else None

    async def _fetch_all(self, bid: Bid, *, attachments: bool, reuse: BidFiles | None) -> list[FileRecord]:
        records: list[FileRecord] = []
        seen: set[str] = set()
        to_scan: list[tuple[FileRecord, bytes]] = []
        if reuse is not None:  # complete a partial fetch: keep its documents, scan them for links
            for record in reuse.files:
                if record.status == "failed":  # fetched again below, when its link is found
                    continue
                records.append(record)
                seen.add(record.url)
                if record.kind == "pdf" and record.path:
                    to_scan.append((record, self.cache.read_file(record)))

        async def fetch_document(url: str, role: Role, link: AttachmentLink | None = None) -> None:
            seen.add(url)
            got = await self._get(bid.b_id, url, role, link)
            records.extend(got[0])
            if got[1] is not None and got[0][0].kind == "pdf":
                to_scan.append((got[0][0], got[1]))

        if reuse is None:
            await fetch_document(self.session.url(document_path(bid)), "bid_document")
            if records[0].status == "failed":
                raise DocumentUnavailable(records[0].error or "unknown error")
        parent = parent_document_path(bid)
        if parent and self.session.url(parent) not in seen:
            await fetch_document(self.session.url(parent), "parent_document")
        if not attachments:
            return records

        jobs = []
        i = 0
        while i < len(to_scan):  # a parent bid found by link is scanned too
            _, content = to_scan[i]
            i += 1
            for link in attachment_links(content, allowed=self.session.pool.is_allowed):
                if link.url in seen:
                    continue
                seen.add(link.url)
                if link.kind == "parent_bid":
                    await fetch_document(link.url, "parent_document", link)
                elif link.kind == "gtc":
                    jobs.append(self._get_gtc(link))
                else:
                    jobs.append(self._get(bid.b_id, link.url, "attachment", link))
        for result in await asyncio.gather(*jobs):  # hosts in parallel; each host's client keeps its own pace
            records.extend(result[0] if isinstance(result, tuple) else [result])
        return records

    async def _get(
        self, b_id: int, url: str, role: Role, link: AttachmentLink | None
    ) -> tuple[list[FileRecord], bytes | None]:
        base = {"role": role, "url": url, "source_page": link.page if link else None,
                "link_kind": link.kind if link else None}
        try:
            got = await download(self.session.pool, url)
        except (GemHttpError, HostNotAllowed, httpx.HTTPError) as exc:
            log.warning("download failed %s: %s", url, exc)
            name = url.rstrip("/").rsplit("/", 1)[-1] or "download"
            return [FileRecord(**base, filename=name, size=0, kind="empty", status="failed", error=str(exc))], None
        return await self._process(b_id, got.content, got.filename, base), got.content

    async def _process(self, b_id: int, content: bytes, filename: str, base: dict) -> list[FileRecord]:
        kind = classify(content)
        if kind == "empty":
            return [FileRecord(**base, filename=filename, size=0, kind=kind, status="empty")]
        sha, path = self.cache.store_file(b_id, content, suffix_for(kind, filename, content))
        record = FileRecord(**base, filename=filename, size=len(content), kind=kind, status="ok", sha256=sha,
                            path=path)
        if kind == "archive":
            return [record, *await self._members(b_id, content, base)]
        pages = await asyncio.to_thread(extract_text, content, kind, self.ocr)
        if pages is None:
            record.status = "unsupported"
            return [record]
        record.pages = len(pages)
        record.ocr_pages = [p.page for p in pages if p.method == "ocr"]
        record.text_path = self.cache.store_text(path, pages)
        if is_placeholder(pages):
            record.status = "placeholder"
        return [record]

    async def _members(self, b_id: int, content: bytes, base: dict) -> list[FileRecord]:
        try:
            members = safe_members(content)
        except ArchiveError as exc:
            log.warning("archive skipped %s: %s", base["url"], exc)
            return []
        out: list[FileRecord] = []
        for member in members:
            member_base = {**base, "role": "archive_member", "url": f"{base['url']}#{member.name}"}
            out.extend(await self._process(b_id, member.content, member.name, member_base))
        return out

    async def _get_gtc(self, link: AttachmentLink) -> FileRecord:
        """GeM's general terms: one file per date, shared by every bid."""
        date = httpx.URL(link.url).params.get("date", "unknown")
        base = {"role": "gtc", "url": link.url, "source_page": link.page, "link_kind": "gtc"}
        lock = self._gtc_locks.setdefault(date, asyncio.Lock())
        async with lock:
            path = self.cache.gtc(date)
            if path is None:
                try:
                    got = await download(self.session.pool, link.url)
                except (GemHttpError, HostNotAllowed, httpx.HTTPError) as exc:
                    return FileRecord(**base, filename=f"gtc-{date}.pdf", size=0, kind="empty", status="failed",
                                      error=str(exc))
                if classify(got.content) != "pdf":
                    return FileRecord(**base, filename=f"gtc-{date}.pdf", size=got.size, kind=classify(got.content),
                                      status="failed", error="not a PDF")
                path = self.cache.store_gtc(date, got.content)
                pages = await asyncio.to_thread(extract_text, got.content, "pdf", self.ocr)
                self.cache.store_text(path, pages or [])
        size = (self.cache.root / path).stat().st_size
        record = FileRecord(**base, filename=f"gtc-{date}.pdf", size=size, kind="pdf", status="ok", path=path,
                            text_path=path + ".text.json")
        record.pages = len(self.cache.load_text(record))
        return record

