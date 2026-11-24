"""Document cache: cache/docs/<b_id>/ holds each file by SHA-256, its text per page, and a manifest.
GeM's general terms, identical for every bid on a date, live once in cache/gtc/<date>.pdf."""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, TypeAdapter

from gem_agent.tools.gem.corrigendum import Corrigendum
from gem_agent.tools.pdf.text import PageText

Role = Literal["bid_document", "parent_document", "attachment", "gtc", "archive_member"]
Status = Literal["ok", "placeholder", "empty", "failed", "unsupported"]
_PAGES = TypeAdapter(list[PageText])


class FileRecord(BaseModel):
    role: Role
    url: str
    filename: str
    size: int
    kind: str  # pdf | html | office | archive | empty | other
    status: Status
    source_page: int | None = None  # page of the bid PDF that linked to it
    link_kind: str | None = None
    sha256: str | None = None
    path: str | None = None  # relative to the cache root
    text_path: str | None = None
    pages: int = 0
    ocr_pages: list[int] = []
    error: str | None = None
    # names people can read (cache/names.py), filled once per file; text_key None = not named yet
    label: str | None = None  # the slot the bid document lists it under ("Scope of Work")
    title: str | None = None  # its own heading, or GeM's fixed page it is
    text_key: str | None = None  # hash of its text: copies with the same text share it ("" = no text)


class BidFiles(BaseModel):
    b_id: int
    bid_number: str
    fetched_at: datetime
    corrigendum_flag: bool
    corrigendum: Corrigendum | None = None
    files: list[FileRecord]
    partial: bool = False  # only the bid document (and an RA's original bid) so far; attachments not fetched
    buyer_org: str | None = None  # from the bid document's first page; the listing has only ministry and department

    @property
    def main_ok(self) -> bool:
        return bool(self.files) and self.files[0].status == "ok"

    @property
    def complete_ok(self) -> bool:
        return not any(f.status == "failed" for f in self.files if f.role != "gtc")

    def readable(self) -> list[FileRecord]:
        """Files with real text: placeholders, empty and failed files are left out."""
        return [f for f in self.files if f.status == "ok" and f.text_path]


class DocCache:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _write(self, rel: str, data: bytes) -> None:
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(target)

    def store_file(self, b_id: int, content: bytes, suffix: str) -> tuple[str, str]:
        sha = hashlib.sha256(content).hexdigest()
        rel = f"docs/{b_id}/files/{sha}{suffix}"
        if not (self.root / rel).exists():
            self._write(rel, content)
        return sha, rel

    def read_file(self, record: FileRecord) -> bytes:
        assert record.path
        return (self.root / record.path).read_bytes()

    def store_text(self, rel: str, pages: list[PageText]) -> str:
        text_rel = rel + ".text.json"
        self._write(text_rel, _PAGES.dump_json(pages))
        return text_rel

    def load_text(self, record: FileRecord) -> list[PageText]:
        if not record.text_path:
            return []
        return _PAGES.validate_json((self.root / record.text_path).read_bytes())

    def gtc(self, date: str) -> str | None:
        rel = f"gtc/{date}.pdf"
        return rel if (self.root / rel).exists() else None

    def store_gtc(self, date: str, content: bytes) -> str:
        rel = f"gtc/{date}.pdf"
        self._write(rel, content)
        return rel

    def load(self, b_id: int) -> BidFiles | None:
        path = self.root / f"docs/{b_id}/manifest.json"
        return BidFiles.model_validate_json(path.read_bytes()) if path.exists() else None

    def save(self, files: BidFiles) -> None:
        self._write(f"docs/{files.b_id}/manifest.json", files.model_dump_json(indent=1).encode())

