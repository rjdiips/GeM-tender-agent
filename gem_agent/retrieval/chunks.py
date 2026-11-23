"""Page-sized chunking of bid documents."""
from __future__ import annotations

from pydantic import BaseModel

from gem_agent.cache.docs import BidFiles, DocCache, FileRecord
from gem_agent.tools.pdf.text import text_chars


class Chunk(BaseModel):
    label: str  # F1, F2 … in file order, used to cite evidence
    filename: str
    role: str
    page: int
    text: str


def file_labels(files: BidFiles, include_gtc: bool = False) -> list[tuple[str, FileRecord]]:
    readable = [f for f in files.readable() if include_gtc or f.role != "gtc"]
    return [(f"F{i}", f) for i, f in enumerate(readable, start=1)]


def make_chunks(files: BidFiles, cache: DocCache, *, max_chars: int = 3000, overlap: int = 200,
                include_gtc: bool = False) -> list[Chunk]:
    """One chunk per page; pages longer than max_chars (docx, xlsx sheets) become overlapping windows.
    GeM's general terms are the same for every bid, so they are left out unless asked for."""
    chunks = []
    for label, record in file_labels(files, include_gtc):
        for page in cache.load_text(record):
            text = page.text.strip()
            if text_chars(text) < 5:  # blank page; short pages can still matter
                continue
            start = 0
            while start < len(text):
                chunks.append(Chunk(label=label, filename=record.filename, role=record.role, page=page.page,
                                    text=text[start : start + max_chars]))
                if start + max_chars >= len(text):
                    break
                start += max_chars - overlap
    return chunks
