"""Safe archive listing and extraction (spec 12): listed before reading, paths outside the folder rejected,
sizes capped. Members are read into memory, never written where the archive says."""
from __future__ import annotations

import io
import logging
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

log = logging.getLogger(__name__)


class ArchiveError(Exception):
    """Unreadable, or larger than allowed."""


@dataclass
class Member:
    name: str
    content: bytes


def _safe_name(name: str) -> bool:
    path = PurePosixPath(name.replace("\\", "/"))
    return bool(name) and not path.is_absolute() and ".." not in path.parts and ":" not in path.parts[0]


def safe_members(content: bytes, *, max_members: int = 200, max_total_bytes: int = 300_000_000) -> list[Member]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ArchiveError(f"not a readable zip: {exc}") from exc
    infos = [i for i in archive.infolist() if not i.is_dir()]
    if len(infos) > max_members:
        raise ArchiveError(f"{len(infos)} members (limit {max_members})")
    if sum(i.file_size for i in infos) > max_total_bytes:
        raise ArchiveError(f"uncompressed size over {max_total_bytes} bytes")
    members = []
    for info in infos:
        if not _safe_name(info.filename):
            log.warning("archive member rejected: %r", info.filename)
            continue
        members.append(Member(info.filename, archive.read(info)))
    return members
