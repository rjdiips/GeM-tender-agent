"""bids.sqlite: every open bid's listing data plus its title embedding (spec 6.2)."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

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


def _text_hash(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


@dataclass
class UpsertCounts:
    new: int = 0
    updated: int = 0
    unchanged: int = 0


@dataclass
class CandidateRow:
    b_id: int
    text: str
    closing: str | None
    raw: str

    def bid(self) -> Bid:
        return Bid.from_listing(json.loads(self.raw))


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
        self._vector_cache: dict[str, tuple[str | None, np.ndarray, np.ndarray]] = {}

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
                    f"SELECT {', '.join(_TRACKED)}, card FROM bids WHERE b_id = ?", (bid.b_id,)
                ).fetchone()
                card = old[-1] if old else None
                old = old[:-1] if old else None
                if old is None:
                    counts.new += 1
                elif tuple(old) != tuple(values[c] for c in _TRACKED):
                    counts.updated += 1
                else:
                    counts.unchanged += 1
                cols = ["b_id", *values, "raw", "embed_text", "state", "missed_refreshes", "first_seen", "last_seen"]
                row = [bid.b_id, *values.values(), json.dumps(bid.raw), embed_text(bid, card), "open", 0, seen, seen]
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

    def vague_without_card(self, **filters: object) -> list[Bid]:
        """Open vague-title bids with no usable card (pending, or tried and nothing found) within the filters: no
        word or meaning search can reach them, so the Searcher adds them to every pool."""
        from gem_agent.tools.gem.titles import is_vague_title

        keys = ("published_from", "published_to", "closing_from", "closing_to", "ministries", "departments",
                "bid_types", "include_closed")
        defaults: dict[str, object] = {"ministries": (), "departments": (), "bid_types": (), "include_closed": False}
        where_sql, args = self._where(*[filters.get(k, defaults.get(k)) for k in keys])  # type: ignore[arg-type]
        sql = ("SELECT raw, title FROM bids" + (where_sql + " AND " if where_sql else " WHERE ") +
               "(card IS NULL OR card = '') AND (title LIKE 'Hiring of Agency for IT Projects%' OR "
               "title LIKE 'Custom Bid for Services%')")
        return [Bid.from_listing(json.loads(raw)) for raw, title in self._conn.execute(sql, args) if is_vague_title(title)]

    def needing_cards(self, limit: int | None = None) -> list[Bid]:
        """Open bids whose title says nothing about the subject and that have no card yet, closing soonest first."""
        from gem_agent.tools.gem.titles import is_vague_title

        rows = self._conn.execute(
            "SELECT raw, title FROM bids WHERE state = 'open' AND card IS NULL AND "
            "(title LIKE 'Hiring of Agency for IT Projects%' OR title LIKE 'Custom Bid for Services%') ORDER BY closing")
        out = [Bid.from_listing(json.loads(raw)) for raw, title in rows if is_vague_title(title)]
        return out[:limit] if limit else out

    def bid_state(self, b_id: int) -> str | None:
        row = self._conn.execute("SELECT state FROM bids WHERE b_id = ?", (b_id,)).fetchone()
        return row[0] if row else None

    def known(self, b_ids: Sequence[int]) -> set[int]:
        if not b_ids:
            return set()
        marks = ",".join("?" * len(b_ids))
        return {r[0] for r in self._conn.execute(f"SELECT b_id FROM bids WHERE b_id IN ({marks})", list(b_ids))}

    def close_expired(self, *, now: datetime) -> int:
        """Open bids past their closing date become closed; their embeddings are dropped (spec 11.1)."""
        with self._lock, self._conn:
            ids = [r[0] for r in self._conn.execute(
                "SELECT b_id FROM bids WHERE state = 'open' AND closing < ?", (_iso(now),))]
            self._conn.executemany("UPDATE bids SET state = 'closed' WHERE b_id = ?", [(i,) for i in ids])
            self._conn.executemany("DELETE FROM embeddings WHERE b_id = ?", [(i,) for i in ids])
            if ids:
                self._bump_embed_version()
        return len(ids)

    def mark_missed(self, *, refresh_started: datetime, gone_after: int) -> tuple[int, int]:
        """After a complete full refresh: open bids it did not see are missed once more; after
        `gone_after` misses in a row they are gone. A 1.6 h scan of a shifting list can skip a few bids,
        so one miss alone is not trusted. Returns (missed now, newly gone)."""
        started = _iso(refresh_started)
        with self._lock, self._conn:
            missed = self._conn.execute(
                "UPDATE bids SET missed_refreshes = missed_refreshes + 1 WHERE state = 'open' AND last_seen < ?",
                (started,),
            ).rowcount
            gone = [r[0] for r in self._conn.execute(
                "SELECT b_id FROM bids WHERE state = 'open' AND missed_refreshes >= ?", (gone_after,))]
            self._conn.executemany("UPDATE bids SET state = 'gone' WHERE b_id = ?", [(i,) for i in gone])
            self._conn.executemany("DELETE FROM embeddings WHERE b_id = ?", [(i,) for i in gone])
            if gone:
                self._bump_embed_version()
        return missed, len(gone)

    def candidates(
        self,
        *,
        published_from: datetime | None = None,
        published_to: datetime | None = None,
        closing_from: datetime | None = None,
        closing_to: datetime | None = None,
        ministries: Sequence[str] = (),
        departments: Sequence[str] = (),
        bid_types: Sequence[int] = (),
        include_closed: bool = False,
    ) -> list[CandidateRow]:
        where_sql, args = self._where(published_from, published_to, closing_from, closing_to, ministries, departments,
                                      bid_types, include_closed)
        sql = "SELECT b_id, embed_text, closing, raw FROM bids" + where_sql
        return [CandidateRow(*r) for r in self._conn.execute(sql, args)]

    def count(self, **filters: object) -> int:
        """How many bids match the same filters as candidates()."""
        keys = ("published_from", "published_to", "closing_from", "closing_to", "ministries", "departments",
                "bid_types", "include_closed")
        defaults: dict[str, object] = {"ministries": (), "departments": (), "bid_types": (), "include_closed": False}
        values = [filters.get(k, defaults.get(k)) for k in keys]
        where_sql, args = self._where(*values)  # type: ignore[arg-type]
        return self._conn.execute("SELECT COUNT(*) FROM bids" + where_sql, args).fetchone()[0]

    def distinct(self, column: str) -> list[tuple[str, int]]:
        """Values of ministry or department among open bids, most bids first."""
        assert column in ("ministry", "department")
        sql = (f"SELECT {column}, COUNT(*) n FROM bids WHERE state = 'open' AND {column} IS NOT NULL "
               f"GROUP BY {column} ORDER BY n DESC")
        return [(r[0], r[1]) for r in self._conn.execute(sql)]

    @staticmethod
    def _where(
        published_from: datetime | None, published_to: datetime | None, closing_from: datetime | None,
        closing_to: datetime | None, ministries: Sequence[str], departments: Sequence[str], bid_types: Sequence[int],
        include_closed: bool,
    ) -> tuple[str, list[object]]:
        where: list[str] = []
        args: list[object] = []
        if not include_closed:
            where.append("state = 'open'")
        for col, op, value in (("published", ">=", published_from), ("published", "<=", published_to),
                               ("closing", ">=", closing_from), ("closing", "<=", closing_to)):
            if value:
                where.append(f"{col} {op} ?")
                args.append(_iso(value))
        for col, values in (("ministry", ministries), ("department", departments), ("bid_type", bid_types)):
            if values:
                where.append(f"{col} IN ({','.join('?' * len(values))})")
                args.extend(values)
        return (" WHERE " + " AND ".join(where) if where else ""), args

    # --- embeddings ---------------------------------------------------------------------------------------------

    def pending_embeddings(self, model: str, limit: int | None = None) -> list[tuple[int, str]]:
        """Open bids with no embedding for this model, or whose text changed since."""
        sql = ("SELECT b.b_id, b.embed_text, e.text_hash FROM bids b "
               "LEFT JOIN embeddings e ON e.b_id = b.b_id AND e.model = ? WHERE b.state = 'open' ORDER BY b.b_id")
        out = []
        for b_id, text, stored_hash in self._conn.execute(sql, (model,)):
            if stored_hash != _text_hash(text):
                out.append((b_id, text))
                if limit and len(out) >= limit:
                    break
        return out

    def store_embeddings(self, model: str, items: Iterable[tuple[int, str, np.ndarray]]) -> None:
        rows = [(b_id, model, _text_hash(text), int(vec.shape[0]), np.asarray(vec, np.float16).tobytes())
                for b_id, text, vec in items]
        with self._lock, self._conn:
            self._conn.executemany("INSERT OR REPLACE INTO embeddings VALUES (?, ?, ?, ?, ?)", rows)
            self._bump_embed_version()

    def vectors(self, model: str) -> tuple[np.ndarray, np.ndarray]:
        """(b_ids, float32 matrix) of every stored embedding for the model, cached until embeddings change,
        including changes made by another process (the sync job)."""
        version = self.state("embed_version")
        cached = self._vector_cache.get(model)
        if cached and cached[0] == version:
            return cached[1], cached[2]
        rows = self._conn.execute(
            "SELECT b_id, dim, vector FROM embeddings WHERE model = ? ORDER BY b_id", (model,)).fetchall()
        ids = np.array([r[0] for r in rows], dtype=np.int64)
        matrix = (np.stack([np.frombuffer(r[2], np.float16) for r in rows]).astype(np.float32)
                  if rows else np.zeros((0, 0), np.float32))
        self._vector_cache[model] = (version, ids, matrix)
        return ids, matrix

    def _bump_embed_version(self) -> None:
        self._conn.execute(
            "INSERT INTO sync_state VALUES ('embed_version', '1') "
            "ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1")

    # --- sync state and stats -----------------------------------------------------------------------------------

    def state(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_state(self, key: str, value: str | None) -> None:
        with self._lock, self._conn:
            if value is None:
                self._conn.execute("DELETE FROM sync_state WHERE key = ?", (key,))
            else:
                self._conn.execute("INSERT OR REPLACE INTO sync_state VALUES (?, ?)", (key, value))

    def stats(self) -> dict[str, object]:
        counts = dict(self._conn.execute("SELECT state, COUNT(*) FROM bids GROUP BY state").fetchall())
        embedded = self._conn.execute("SELECT model, COUNT(*) FROM embeddings GROUP BY model").fetchall()
        state = dict(self._conn.execute("SELECT key, value FROM sync_state").fetchall())
        return {"open": counts.get("open", 0), "closed": counts.get("closed", 0), "gone": counts.get("gone", 0),
                "embedded": dict(embedded), **state}
