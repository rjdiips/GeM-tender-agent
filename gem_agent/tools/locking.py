"""Cross-platform advisory file locks: fcntl.flock on POSIX, msvcrt.locking on Windows.

The lock file holds one byte, so a byte-range lock can be taken on the same region everywhere. The handle is
returned by `acquire` and must be closed by `release`; `locked` is the blocking context-manager form.
"""
from __future__ import annotations

import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO

if sys.platform == "win32":
    import msvcrt

    def _try(handle: IO[bytes]) -> bool:
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(handle: IO[bytes]) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try(handle: IO[bytes]) -> bool:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False

    def _unlock(handle: IO[bytes]) -> None:
        fcntl.flock(handle, fcntl.LOCK_UN)


def acquire(path: Path, *, blocking: bool = True, poll: float = 0.05) -> IO[bytes] | None:
    """Open `path` and take its exclusive lock; None when non-blocking and another process holds it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")  # noqa: SIM115 - the handle is the lock; release() closes it
    if path.stat().st_size == 0:
        handle.write(b"\0")
        handle.flush()
    while True:
        if _try(handle):
            return handle
        if not blocking:
            handle.close()
            return None
        time.sleep(poll)


def release(handle: IO[bytes]) -> None:
    try:
        _unlock(handle)
    finally:
        handle.close()


@contextmanager
def locked(path: Path, *, poll: float = 0.05) -> Iterator[None]:
    handle = acquire(path, poll=poll)
    if handle is None:  # pragma: no cover - acquire(blocking=True) never returns None
        raise RuntimeError(f"could not lock {path}")
    try:
        yield
    finally:
        release(handle)
