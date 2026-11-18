"""Command-line entry points: chat, index sync, jobs."""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import timedelta

from gem_agent.config import Settings, get_settings
from gem_agent.index.db import BidIndex
from gem_agent.index.query import IndexQuery, search_index
from gem_agent.index.sync import NeedFullLoad, SyncBusy, embed_pending, full_load, sync_lock, sync_recent
from gem_agent.retrieval.embed import BgeM3Embedder, pick_device
from gem_agent.tools.gem.models import site_now
from gem_agent.tools.http_client import BidplusSession, ClientPool

log = logging.getLogger("gem_agent")


def _embedder(settings: Settings) -> BgeM3Embedder:
    device = pick_device(settings.embed_device, min_free_mb=settings.embed_min_free_gpu_mb)
    return BgeM3Embedder(settings.embed_model, device=device)


async def _write_cards(settings: Settings, index: BidIndex, session: BidplusSession, limit: int,
                       redo: bool = False) -> None:
    """Cards for vague-title bids (index/cards.py), before embedding, so the new text is embedded in the same run."""
    from gem_agent.agents.collector import Collector
    from gem_agent.agents.models import chat_model
    from gem_agent.cache.docs import DocCache
    from gem_agent.index.cards import clear_generic_cards, write_cards

    collector = Collector(session, DocCache(settings.cache_dir))
    if redo:
        log.info("cards: cleared all %d to rebuild them", index.clear_cards())
    cleared = clear_generic_cards(index)
    if cleared:
        log.info("cards: cleared %d that only restated the title", cleared)
    log.info("cards: %s", await write_cards(index, collector, chat_model(settings), limit=limit))


async def _index_job(settings: Settings, job: str, max_pages: int | None, card_limit: int = 40,
                     redo: bool = False) -> None:
    index = BidIndex(settings.index_path)
    pool = ClientPool.shared(settings)
    try:
        session = BidplusSession(pool)
        if job == "load":
            report = await full_load(index, session, max_pages=max_pages,
                                     gone_after=settings.index_gone_after_missed_refreshes)
        elif job == "sync":
            report = await sync_recent(index, session, margin=timedelta(minutes=settings.index_sync_margin_min))
        else:  # cards only
            report = None
        if report is not None:
            log.info("%s: %s", job, report)
        if getattr(report, "complete", True):
            await _write_cards(settings, index, session, card_limit, redo)
            log.info("embedded %d titles", embed_pending(index, _embedder(settings)))
    finally:
        await pool.aclose()
        index.close()


def cmd_index(args: argparse.Namespace, settings: Settings) -> int:
    if args.action in ("load", "sync", "cards"):
        try:
            with sync_lock(settings.index_path.with_name("sync.lock")):
                cards = args.action == "cards"
                asyncio.run(_index_job(settings, args.action, args.max_pages, args.limit if cards else 40,
                                       redo=cards and args.redo))
        except SyncBusy:
            log.info("another index job is running; skipping this %s", args.action)
        except NeedFullLoad:
            log.error("no full load yet: run `gem-agent index load` first")
            return 2
        return 0
    index = BidIndex(settings.index_path)
    try:
        if args.action == "embed":
            log.info("embedded %d titles", embed_pending(index, _embedder(settings)))
        elif args.action == "stats":
            for key, value in index.stats().items():
                print(f"{key}: {value}")
        elif args.action == "query":
            from gem_agent.retrieval.rerank import BgeReranker

            q = IndexQuery(text=args.text, terms=args.term or [], limit=args.limit,
                           published_from=site_now() - timedelta(days=args.days) if args.days else None)
            reranker = None if args.no_rerank else BgeReranker(settings.rerank_model)
            for hit in search_index(index, q, _embedder(settings), reranker):
                b = hit.bid
                score = f"{hit.rerank:.3f}" if hit.rerank is not None else f"{hit.fused:.4f}"
                print(f"{score}  {b.bid_number:<22} closes {b.closing:%d %b %H:%M}  {b.title[:90]}")
    finally:
        index.close()
    return 0


def _when(iso: str) -> str:
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    d, t = iso.split("T")
    return f"{int(d[8:10])} {months[int(d[5:7]) - 1]} {t[:5]}"


def render_event(event: dict) -> str | None:
    """One terminal line per chat event."""
    kind = event.get("type")
    if kind == "started":
        return f"run {event['run']['run_id']}"
    if kind == "understood":
        u = event["understood"]
        return f"{u['summary']} ({u['window_count']:,} open bids in that window; {u['likely']} look relevant from their titles)"
    if kind == "progress":
        return f"{event['event']['at'][11:19]}  {event['event']['text']}"
    if kind == "result":
        b = event["bid"]
        mark = "✔" if b["relevant"] else "✘"
        closing = _when(b["closing"]) if b.get("closing") else "?"
        return f"  {mark} {b['bid_number']}  closes {closing}  {b['title'][:70]}\n      {b['reason']}"
    if kind == "message":
        return event["text"]
    if kind == "show":
        return f"(see the {event['tab']} tab)"
    if kind == "done":
        return f"[{event['status']}]"
    return None


async def _chat(settings: Settings, user: str, text: str, run_id: str | None) -> None:
    from gem_agent.app.factory import build_services
    from gem_agent.graph.manager import RunManager

    services, _, on_start = build_services(settings)
    for hook in on_start:
        await hook()
    async for event in RunManager(services).ask(user, text, run_id=run_id):
        line = render_event(event)
        if line:
            print(line, flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gem-agent")
    sub = parser.add_subparsers(dest="command", required=True)
    chat = sub.add_parser("chat", help="ask for tenders from the command line, results streamed")
    chat.add_argument("text")
    chat.add_argument("--user", default="cli")
    chat.add_argument("--run", help="follow up on this run id")
    serve = sub.add_parser("serve", help="the web chat (FastAPI)")
    serve.add_argument("--port", type=int)
    idx = sub.add_parser("index", help="local open-bid index (spec 6.2)")
    idx.add_argument("action", choices=["load", "sync", "cards", "embed", "stats", "query"],
                     help="load: full load / nightly re-fetch; sync: newest bids since the last sync")
    idx.add_argument("text", nargs="?", default="", help="query: meaning to search for")
    idx.add_argument("--term", action="append", help="query: word-search term (repeatable)")
    idx.add_argument("--days", type=int, help="query: published within the last N days")
    idx.add_argument("--limit", type=int, default=20)
    idx.add_argument("--no-rerank", action="store_true")
    idx.add_argument("--max-pages", type=int, help="load: stop after N pages (resumes next time)")
    idx.add_argument("--redo", action="store_true", help="cards: clear every card first and rebuild them all")
    ev = sub.add_parser("eval", help="score the Judge against the labelled set (spec 13)")
    ev.add_argument("--set", default="eval/labelled_set.json", help="labelled set file")
    ev.add_argument("--topic", help="only this topic")
    ev.add_argument("--parallel", type=int, default=3)
    ev.add_argument("--out", help="write each bid's label and verdict here (JSON)")
    bf = sub.add_parser("backfill-queries", help="add the last date for queries to results judged before 17 Nov")
    bf.add_argument("--dry-run", action="store_true", help="report what would be filled, write nothing")
    bf.add_argument("--redo-documents", action="store_true",
                    help="read again the dates taken from buyer documents (GeM pre-bid dates are kept)")
    args = parser.parse_args(argv)
    settings = get_settings()
    if args.command == "chat":
        logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
        asyncio.run(_chat(settings, args.user, args.text, args.run))
        return 0
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.command == "serve":
        import uvicorn

        uvicorn.run("gem_agent.app.main:app", factory=True, host=settings.app_host, port=args.port or settings.app_port)
        return 0
    if args.command == "eval":
        return asyncio.run(_eval(settings, args))
    if args.command == "backfill-queries":
        return asyncio.run(_backfill_queries(settings, args.dry_run, args.redo_documents))
    return cmd_index(args, settings)


async def _eval(settings: Settings, args: argparse.Namespace) -> int:
    from pathlib import Path

    from gem_agent.app.factory import build_services
    from gem_agent.eval.labelled import LabelledSet, report, rows_json
    from gem_agent.eval.run import judge_set

    services, cache, _ = build_services(settings)
    labelled = LabelledSet.load(Path(args.set))
    rows = await judge_set(labelled, services.judge, cache, BidIndex(settings.index_path), services.collector,
                           topic=args.topic, parallel=args.parallel)
    print(report(rows))
    if args.out:
        Path(args.out).write_text(rows_json(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())


async def _backfill_queries(settings: Settings, dry_run: bool, redo_documents: bool) -> int:
    from gem_agent.agents.judge import Judge
    from gem_agent.agents.models import chat_model
    from gem_agent.cache.docs import DocCache
    from gem_agent.cache.verdicts import VerdictCache
    from gem_agent.graph.store import RunStore
    from gem_agent.jobs.backfill_queries import backfill_queries
    from gem_agent.retrieval.embed import SmallEmbedder

    cache, verdicts = DocCache(settings.cache_dir), VerdictCache(settings.cache_dir)
    judge = Judge(chat_model(settings), cache, verdicts, embedder=SmallEmbedder(settings.judge_cpu_embed_model))
    report = await backfill_queries(judge, cache, verdicts, RunStore(settings.runs_dir), dry_run=dry_run,
                                    redo_documents=redo_documents)
    for bid_number, when in sorted(report.dates.items()):
        print(f"{bid_number}  queries {when}")
    print(f"{'would fill' if dry_run else 'filled'} {report.filled} results, cleared {report.cleared}, "
          f"{report.model_calls} model calls, "
          f"{report.no_documents} bids without cached documents")
    return 0

