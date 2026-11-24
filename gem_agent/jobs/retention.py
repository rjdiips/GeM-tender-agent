"""Retention: a bid's cached documents and verdicts are deleted once they are past their window after closing.

Documents of a bid that a run found relevant are kept longer than those of a quarantined bid; verdicts go sooner for
every bid. A bid in a running run, a bid in the labelled eval set and a bid in no run are never touched, and neither is
cache/gtc/. Run on demand, at most hourly (see the README)."""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from gem_agent.app.factory import labelled_b_ids
from gem_agent.cli import labelled_path
from gem_agent.config import Settings
from gem_agent.graph.store import RunStore
from gem_agent.graph.views import bid_state


@dataclass
class RetentionReport:
    bids: int = 0
    files: int = 0
    bytes: int = 0


@dataclass
class _Bid:
    closing: datetime | None
    relevant: bool = False


def _status(run_json: Path) -> str:
    """A run.json that cannot be read counts as running: its bids are then kept."""
    try:
        return json.loads(run_json.read_text(encoding="utf-8")).get("status", "running")
    except (OSError, ValueError, AttributeError):
        return "running"


def _shortlisted(store: RunStore, run_id: str) -> set[int]:
    """Ids on a run's shortlist. A running run has none early on, and a half-written file is not fatal."""
    path = store.root / run_id / "shortlist.json"
    try:
        return {row["b_id"] for row in json.loads(path.read_text(encoding="utf-8"))}
    except (OSError, ValueError, TypeError, KeyError):
        return set()


def _read_runs(settings: Settings) -> dict[int, _Bid]:
    """Each bid of the finished runs with its closing date and whether any run found it relevant. A bid that appears
    in any running run — as an outcome or on its shortlist, before it has been read — is left out altogether."""
    store = RunStore(settings.runs_dir)
    running: set[int] = set()
    bids: dict[int, _Bid] = {}
    for run_json in sorted(settings.runs_dir.glob("*/run.json")):
        run_id = run_json.parent.name
        is_running = _status(run_json) == "running"
        if is_running:
            running |= _shortlisted(store, run_id)
        for b_id, o in store.outcomes(run_id).items():
            if is_running:
                running.add(b_id)
            else:
                entry = bids.setdefault(b_id, _Bid(closing=o.bid.closing))
                entry.relevant |= bid_state(o) == "relevant"
    return {b_id: b for b_id, b in bids.items() if b_id not in running}


def _expired(settings: Settings, b_id: int, bid: _Bid, now: datetime) -> list[Path]:
    """The cache directories of one bid whose window after closing has passed and which are on disk."""
    if bid.closing is None:
        return []
    docs_days = settings.retain_kept_after_close_days if bid.relevant else settings.retain_quarantine_days
    targets = []
    if bid.closing + timedelta(days=docs_days) < now:
        targets.append(settings.cache_dir / "docs" / str(b_id))
    if bid.closing + timedelta(days=settings.retain_verdict_after_close_days) < now:
        targets.append(settings.cache_dir / "verdicts" / str(b_id))
    return [t for t in targets if t.exists()]


def _size(path: Path) -> tuple[int, int]:
    files = [p for p in path.rglob("*") if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


def prune_caches(settings: Settings, now: datetime, *, dry_run: bool, max_bids: int = 2000) -> RetentionReport:
    """Delete the expired documents and verdicts of up to `max_bids` bids, one bid at a time, in bid-number order."""
    bids = _read_runs(settings)
    labelled = labelled_b_ids(labelled_path(None), strict=True)  # a set that exists but cannot be read stops the pass
    report = RetentionReport()
    for b_id in sorted(bids):
        if report.bids >= max_bids:
            break
        if b_id in labelled:
            continue
        targets = _expired(settings, b_id, bids[b_id], now)
        if not targets:
            continue
        for target in targets:
            files, size = _size(target)
            report.files += files
            report.bytes += size
            if not dry_run:
                shutil.rmtree(target)
        report.bids += 1
    return report
