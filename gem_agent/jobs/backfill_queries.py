"""Fill in the last date for queries on results judged before 17 Nov, when verdicts started carrying it.

GeM's own pre-bid date is read from the bid document with no model. Otherwise one small model call reads the queries
passages, only for results a user sees as relevant (relevant, or restored from quarantine) and judged past triage.
Each bid is worked out once, however many runs show it. Run outcomes and the Judge's verdict cache are both updated.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from gem_agent.agents.judge import Judge
from gem_agent.agents.schemas import QueriesDeadline
from gem_agent.cache.docs import DocCache
from gem_agent.cache.verdicts import VerdictCache
from gem_agent.graph.store import Outcome, RunStore

log = logging.getLogger(__name__)


@dataclass
class BackfillReport:
    filled: int = 0  # results given a date
    cleared: int = 0  # dates read from documents that a redo no longer finds
    model_calls: int = 0
    no_documents: int = 0
    dates: dict[str, str] = field(default_factory=dict)  # bid number -> date, for the log


async def backfill_queries(judge: Judge, cache: DocCache, verdicts: VerdictCache, store: RunStore, *,
                           dry_run: bool = False, redo_documents: bool = False) -> BackfillReport:
    """redo_documents: read again the dates taken from buyer documents (GeM's pre-bid dates are exact and kept)."""
    report = BackfillReport()
    todo: dict[int, list[tuple[str, Outcome]]] = {}
    for run_dir in sorted(store.root.iterdir()):
        if not store.exists(run_dir.name):
            continue
        for o in store.outcomes(run_dir.name).values():
            q = o.verdict.queries if o.verdict else None
            if o.verdict and (q is None or (redo_documents and q.source == "documents")):
                todo.setdefault(o.b_id, []).append((run_dir.name, o))

    async def one(b_id: int, rows: list[tuple[str, Outcome]]) -> tuple[int, QueriesDeadline | None]:
        files = cache.load(b_id)
        if files is None:
            report.no_documents += 1
            return b_id, None
        ask = any((o.verdict.relevant or o.restored) and o.verdict.stage != "triage" for _, o in rows)
        found, asked = await judge.find_queries(rows[0][1].bid, files, ask=ask)
        report.model_calls += asked
        return b_id, found

    for b_id, found in await asyncio.gather(*(one(b, rows) for b, rows in todo.items())):
        for run_id, o in todo[b_id]:
            if found is None and o.verdict.queries is None:
                continue
            if found is None:
                report.cleared += 1
            else:
                report.filled += 1
                report.dates[o.bid.bid_number] = found.when.isoformat()
            if dry_run:
                continue
            verdict = o.verdict.model_copy(update={"queries": found})
            store.save_outcome(run_id, o.model_copy(update={"verdict": verdict}))
            cached = verdicts.get(verdict.b_id, verdict.topic_key, verdict.doc_hash)
            if cached and (cached.queries is None or cached.queries.source == "documents"):
                verdicts.put(cached.model_copy(update={"queries": found}))
    log.info("queries backfill: %s", report)
    return report
