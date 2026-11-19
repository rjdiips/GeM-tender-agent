"""bids.sqlite, the bid cache: the listing data of every bid a live search has returned, plus its card (spec 6.2)."""
from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from gem_agent.tools.gem.models import Bid

SCHEMA = """
CREATE TABLE IF NOT EXISTS bids (
    b_id INTEGER PRIMARY KEY,
    bid_number TEXT NOT NULL,
    title TEXT,
    category_id TEXT,
    ministry TEXT,
    department TEXT,
    published TEXT,
    closing TEXT,
    bid_type INTEGER,
    eval_type INTEGER,
    parent_b_id INTEGER,
    status INTEGER,
    is_high_value INTEGER,
    total_quantity INTEGER,
    raw TEXT NOT NULL,
    embed_text TEXT NOT NULL,
    card TEXT,                                   -- what a vague-title bid buys, from its bid document (cards.py)
    state TEXT NOT NULL DEFAULT 'open',          -- open | closed | gone
    missed_refreshes INTEGER NOT NULL DEFAULT 0,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS bids_state_published ON bids(state, published);
CREATE INDEX IF NOT EXISTS bids_state_closing ON bids(state, closing);
CREATE INDEX IF NOT EXISTS bids_ministry ON bids(ministry);
CREATE TABLE IF NOT EXISTS embeddings (
    b_id INTEGER NOT NULL,
    model TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vector BLOB NOT NULL,                        -- float16
    PRIMARY KEY (b_id, model)
);
CREATE TABLE IF NOT EXISTS sync_state (key TEXT PRIMARY KEY, value TEXT);
"""

# Columns whose change makes an upsert count as "updated".
_TRACKED = ("bid_number", "title", "category_id", "ministry", "department", "published", "closing", "bid_type",
            "eval_type", "parent_b_id", "status", "is_high_value", "total_quantity")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def embed_text(bid: Bid, card: str | None = None) -> str:
    """What gets embedded and word-searched: title, the bid's card if it has one, then buyer (spec 6.2)."""
    return " | ".join(p for p in (bid.title, card, bid.ministry, bid.department) if p)


@dataclass
class UpsertCounts:
    new: int = 0
    updated: int = 0
    unchanged: int = 0


class BidIndex:
    def __init__(self, path: Path | str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")  # safe with WAL; a crash loses at most the last commit
        self._conn.executescript(SCHEMA)
        columns = {r[1] for r in self._conn.execute("PRAGMA table_info(bids)")}
        if "card" not in columns:  # indexes built before cards existed
            self._conn.execute("ALTER TABLE bids ADD COLUMN card TEXT")
        self._lock = threading.Lock()

    def close(self) -> None:
        self._conn.close()

    # --- listing data -------------------------------------------------------------------------------------------

    def upsert(self, bids: Iterable[Bid], *, seen_at: datetime) -> UpsertCounts:
        """Store bids seen in the open-bids listing: they are open again and not missed."""
        counts = UpsertCounts()
        seen = _iso(seen_at)
        with self._lock, self._conn:
            for bid in bids:
                values = {
                    "bid_number": bid.bid_number, "title": bid.title, "category_id": bid.category_id,
                    "ministry": bid.ministry, "department": bid.department, "published": _iso(bid.published),
                    "closing": _iso(bid.closing), "bid_type": int(bid.bid_type), "eval_type": bid.eval_type,
                    "parent_b_id": bid.parent_b_id, "status": bid.status, "is_high_value": int(bid.is_high_value),
                    "total_quantity": bid.total_quantity,
                }
                old = self._conn.execute(
                    f"SELECT {', '.join(_TRACKED)}, card, raw, state, missed_refreshes FROM bids WHERE b_id = ?",
                    (bid.b_id,)
                ).fetchone()
                raw_json = json.dumps(bid.raw)
                card = old[-4] if old else None
                tracked_same = old is not None and tuple(old[:-4]) == tuple(values[c] for c in _TRACKED)
                if old is None:
                    counts.new += 1
                elif not tracked_same:
                    counts.updated += 1
                else:
                    counts.unchanged += 1
                if tracked_same and old[-3] == raw_json and old[-2] == "open" and old[-1] == 0:
                    continue  # nothing to write: same fields, same listing, already open and not missed
                cols = ["b_id", *values, "raw", "embed_text", "state", "missed_refreshes", "first_seen", "last_seen"]
                row = [bid.b_id, *values.values(), raw_json, embed_text(bid, card), "open", 0, seen, seen]
                updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in ("b_id", "first_seen"))
                self._conn.execute(
                    f"INSERT INTO bids ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
                    f"ON CONFLICT(b_id) DO UPDATE SET {updates}",
                    row,
                )
        return counts

    def get(self, b_id: int) -> Bid | None:
        row = self._conn.execute("SELECT raw FROM bids WHERE b_id = ?", (b_id,)).fetchone()
        return Bid.from_listing(json.loads(row[0])) if row else None

    def card(self, b_id: int) -> str | None:
        row = self._conn.execute("SELECT card FROM bids WHERE b_id = ?", (b_id,)).fetchone()
        return row[0] if row else None

    def set_card(self, b_id: int, card: str) -> None:
        """Store what the bid buys; its embedded text changes, so the next embed run embeds it again."""
        bid = self.get(b_id)
        if bid is None:
            return
        with self._lock, self._conn:
            self._conn.execute("UPDATE bids SET card = ?, embed_text = ? WHERE b_id = ?",
                               (card, embed_text(bid, card), b_id))

    def clear_card(self, b_id: int) -> None:
        """Back to "needs a card" (card NULL); an empty-string card means tried and nothing found."""
        bid = self.get(b_id)
        if bid is None:
            return
        with self._lock, self._conn:
            self._conn.execute("UPDATE bids SET card = NULL, embed_text = ? WHERE b_id = ?", (embed_text(bid), b_id))

    def clear_cards(self) -> int:
        """Every card back to "needs a card", tried ones too: for rebuilding them with a new card writer."""
        rows = self._conn.execute("SELECT b_id FROM bids WHERE card IS NOT NULL").fetchall()
        for (b_id,) in rows:
            self.clear_card(b_id)
        return len(rows)

    def cards(self) -> list[tuple[int, str, str]]:
        """(b_id, title, card) for every bid with a non-empty card."""
        return [tuple(r) for r in self._conn.execute(
            "SELECT b_id, title, card FROM bids WHERE card IS NOT NULL AND card != ''")]

    def needing_cards(self, limit: int | None = None) -> list[Bid]:
        """Open bids whose title says nothing about the subject and that have no card yet, closing soonest first."""
        from gem_agent.tools.gem.titles import is_vague_title

        rows = self._conn.execute(
            "SELECT raw, title FROM bids WHERE state = 'open' AND card IS NULL AND "
            "(title LIKE 'Hiring of Agency for IT Projects%' OR title LIKE 'Custom Bid for Services%') ORDER BY closing")
        out = [Bid.from_listing(json.loads(raw)) for raw, title in rows if is_vague_title(title)]
        return out[:limit] if limit else out

    def prune(self, *, closed_before: datetime) -> int:
        """Delete bids that closed before the cutoff, with their embeddings. Returns the bids deleted."""
        cutoff = _iso(closed_before)
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM embeddings WHERE b_id IN (SELECT b_id FROM bids WHERE closing < ?)", (cutoff,))
            return self._conn.execute("DELETE FROM bids WHERE closing < ?", (cutoff,)).rowcount

    def labelled(self) -> list[tuple[int, str | None]]:
        """(b_id, card) for every bid with a non-NULL card; '' marks a bid whose document was tried."""
        return [(r[0], r[1]) for r in self._conn.execute("SELECT b_id, card FROM bids WHERE card IS NOT NULL")]

    def distinct(self, column: str) -> list[tuple[str, int]]:
        """Values of ministry or department among open bids, most bids first."""
        assert column in ("ministry", "department")
        sql = (f"SELECT {column}, COUNT(*) n FROM bids WHERE state = 'open' AND {column} IS NOT NULL "
               f"GROUP BY {column} ORDER BY n DESC")
        return [(r[0], r[1]) for r in self._conn.execute(sql)]
