"""bids.sqlite, the bid cache: the listing data of every bid a live search has returned, plus its card."""
from __future__ import annotations

import json
import sqlite3
import threading
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

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
CREATE TABLE IF NOT EXISTS descriptions (
    b_id INTEGER PRIMARY KEY,
    doc_hash TEXT NOT NULL,
    subject TEXT NOT NULL,
    terms TEXT NOT NULL,                               -- JSON list
    sources TEXT NOT NULL,                             -- JSON list of file names
    has_scope INTEGER NOT NULL,
    buyer_org TEXT,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    written_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS embeddings (
    b_id INTEGER NOT NULL,
    model TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vector BLOB NOT NULL,                        -- float16
    PRIMARY KEY (b_id, model)
);
CREATE TABLE IF NOT EXISTS sync_state (key TEXT PRIMARY KEY, value TEXT);
-- ministry and department names as GeM's listings write them, from every search a run read: the Planner's buyer
-- choices, wider than the shortlisted bids above (names only, never pruned: a few hundred short rows)
CREATE TABLE IF NOT EXISTS buyers (kind TEXT NOT NULL, name TEXT NOT NULL, PRIMARY KEY (kind, name)) WITHOUT ROWID;
"""

# Columns whose change makes an upsert count as "updated".
_TRACKED = ("bid_number", "title", "category_id", "ministry", "department", "published", "closing", "bid_type",
            "eval_type", "parent_b_id", "status", "is_high_value", "total_quantity")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def embed_text(bid: Bid, card: str | None = None) -> str:
    """What gets embedded and word-searched: title, the bid's card if it has one, then buyer."""
    return " | ".join(p for p in (bid.title, card, bid.ministry, bid.department) if p)


BUYER_NAME_MAX = 120


_UNSAFE = {"Cc", "Cf", "Zl", "Zp"}  # control (newline, tab), format (zero-width, bidi override), line/para separators


def buyer_name(name: str | None) -> str | None:
    """A buyer name as the Planner, the hard filter and `exact` searches all see it: whitespace collapsed, cut to
    BUYER_NAME_MAX; None if it is empty or carries a control, format or line/paragraph-separator character. GeM's
    listings are third-party text bound for the Planner's prompt and enum."""
    if not name or any(unicodedata.category(ch) in _UNSAFE for ch in name):
        return None
    return " ".join(name.split())[:BUYER_NAME_MAX].strip() or None


@dataclass
class UpsertCounts:
    new: int = 0
    updated: int = 0
    unchanged: int = 0


class Description(BaseModel):
    """What a bid's documents say it buys, with the provenance needed to refresh it."""

    b_id: int
    doc_hash: str
    subject: str
    terms: list[str]
    sources: list[str]
    has_scope: bool
    buyer_org: str | None
    model: str
    prompt_version: str
    written_at: datetime

    def text(self) -> str:
        return "; ".join([self.subject, *self.terms]) if self.subject else ""

    def usable(self) -> bool:
        return self.subject != ""

    def current(self, model: str, prompt_version: str) -> bool:
        return self.prompt_version != "card" and self.prompt_version == prompt_version and self.model == model


_DESCRIPTION_FIELDS = ("b_id", "doc_hash", "subject", "terms", "sources", "has_scope", "buyer_org", "model",
                       "prompt_version", "written_at")
_DESCRIPTION_COLUMNS = ", ".join(_DESCRIPTION_FIELDS)


def _description_values(d: Description) -> tuple:
    return (d.b_id, d.doc_hash, d.subject, json.dumps(d.terms), json.dumps(d.sources), int(d.has_scope), d.buyer_org,
            d.model, d.prompt_version, d.written_at.isoformat())


def _description(row: tuple) -> Description:
    return Description(b_id=row[0], doc_hash=row[1], subject=row[2], terms=json.loads(row[3]), sources=json.loads(row[4]),
                       has_scope=bool(row[5]), buyer_org=row[6], model=row[7], prompt_version=row[8],
                       written_at=datetime.fromisoformat(row[9]))


class BidIndex:
    def __init__(self, path: Path | str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")  # safe with WAL; a crash loses at most the last commit
        self._lock = threading.Lock()
        had_descriptions = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'descriptions'").fetchone() is not None
        self._conn.executescript(SCHEMA)
        columns = {r[1] for r in self._conn.execute("PRAGMA table_info(bids)")}
        if "card" not in columns:  # indexes built before cards existed
            self._conn.execute("ALTER TABLE bids ADD COLUMN card TEXT")
        if not had_descriptions:
            cards = self._conn.execute("SELECT b_id, card FROM bids WHERE card IS NOT NULL AND card != ''").fetchall()
            written_at = datetime.now()
            copied = []
            for b_id, card in cards:
                subject, *terms = card.split("; ")
                copied.append(_description_values(Description(
                    b_id=b_id, doc_hash="", subject=subject, terms=terms, sources=[], has_scope=False, buyer_org=None,
                    model="", prompt_version="card", written_at=written_at)))
            with self._conn:
                self._conn.executemany(
                    f"INSERT OR IGNORE INTO descriptions ({_DESCRIPTION_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    copied)

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

    def description(self, b_id: int) -> Description | None:
        row = self._conn.execute(f"SELECT {_DESCRIPTION_COLUMNS} FROM descriptions WHERE b_id = ?", (b_id,)).fetchone()
        return _description(row) if row else None

    def descriptions(self, b_ids: Iterable[int]) -> dict[int, Description]:
        ids = tuple(dict.fromkeys(b_ids))
        if not ids:
            return {}
        placeholders = ", ".join("?" for _ in ids)
        rows = self._conn.execute(
            f"SELECT {_DESCRIPTION_COLUMNS} FROM descriptions WHERE b_id IN ({placeholders})", ids).fetchall()
        return {row[0]: _description(row) for row in rows}

    def put_description(self, d: Description) -> None:
        """Store one description per bid, replacing any previous row."""
        with self._lock, self._conn:
            self._conn.execute(
                f"INSERT OR REPLACE INTO descriptions ({_DESCRIPTION_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                _description_values(d))

    def descriptions_to_refresh(self, model: str, prompt_version: str, limit: int) -> list[Bid]:
        """Open bids with no description or one from another model or prompt, closing soonest first."""
        rows = self._conn.execute(
            "SELECT b.raw FROM bids AS b LEFT JOIN descriptions AS d ON d.b_id = b.b_id "
            "WHERE b.state = 'open' AND (d.b_id IS NULL OR d.model != ? OR d.prompt_version != ?) "
            "ORDER BY (b.closing IS NULL), b.closing, b.b_id LIMIT ?", (model, prompt_version, limit)).fetchall()
        return [Bid.from_listing(json.loads(raw)) for (raw,) in rows]

    def needing_cards(self, limit: int | None = None) -> list[Bid]:
        """Open bids whose title says nothing about the subject and that have no card yet, closing soonest first."""
        from gem_agent.tools.gem.titles import is_vague_title

        rows = self._conn.execute(
            "SELECT raw, title FROM bids WHERE state = 'open' AND card IS NULL AND "
            "(title LIKE 'Hiring of Agency for IT Projects%' OR title LIKE 'Custom Bid for Services%') ORDER BY closing")
        out = [Bid.from_listing(json.loads(raw)) for raw, title in rows if is_vague_title(title)]
        return out[:limit] if limit else out

    def prune(self, *, closed_before: datetime, keep: Iterable[int] = frozenset()) -> int:
        """Delete bids that closed before the cutoff, with their embeddings, except those in `keep` (the labelled
        set's, which `eval` reads). Returns the bids deleted."""
        cutoff = _iso(closed_before)
        with self._lock, self._conn:
            self._conn.execute("CREATE TEMP TABLE IF NOT EXISTS prune_keep (b_id INTEGER PRIMARY KEY)")
            self._conn.execute("DELETE FROM prune_keep")
            self._conn.executemany("INSERT OR IGNORE INTO prune_keep VALUES (?)", ((int(b),) for b in keep))
            doomed = "SELECT b_id FROM bids WHERE closing < ? AND b_id NOT IN (SELECT b_id FROM prune_keep)"
            self._conn.execute(f"DELETE FROM descriptions WHERE b_id IN ({doomed})", (cutoff,))
            self._conn.execute(f"DELETE FROM embeddings WHERE b_id IN ({doomed})", (cutoff,))
            return self._conn.execute(f"DELETE FROM bids WHERE b_id IN ({doomed})", (cutoff,)).rowcount

    def labelled(self) -> list[tuple[int, str | None]]:
        """(b_id, card) for every bid with a non-NULL card; '' marks a bid whose document was tried."""
        return [(r[0], r[1]) for r in self._conn.execute("SELECT b_id, card FROM bids WHERE card IS NOT NULL")]

    def add_buyers(self, bids: Iterable[Bid]) -> int:
        """Remember the ministry and department names these listing rows carry; only new names are written. They
        are third-party text bound for the Planner's prompt and enum, so each is one bounded line (buyer_name)."""
        names = {(kind, clean) for b in bids for kind, name in (("ministry", b.ministry), ("department", b.department))
                 if (clean := buyer_name(name))}
        if not names:
            return 0
        known = set(self._conn.execute("SELECT kind, name FROM buyers").fetchall())
        new = sorted(names - known)
        if new:
            with self._lock, self._conn:
                self._conn.executemany("INSERT OR IGNORE INTO buyers (kind, name) VALUES (?, ?)", new)
        return len(new)

    def buyer_names(self, kind: str) -> list[str]:
        """Ministry or department names seen in any search's listing, sorted."""
        assert kind in ("ministry", "department")
        return [r[0] for r in self._conn.execute("SELECT name FROM buyers WHERE kind = ? ORDER BY name", (kind,))]

    def distinct(self, column: str) -> list[tuple[str, int]]:
        """Values of ministry or department among all the cached bids, most bids first. (Nothing closes rows now:
        `state` only served the retired index's sync.)"""
        assert column in ("ministry", "department")
        sql = (f"SELECT {column}, COUNT(*) n FROM bids WHERE {column} IS NOT NULL "
               f"GROUP BY {column} ORDER BY n DESC")
        return [(r[0], r[1]) for r in self._conn.execute(sql)]
