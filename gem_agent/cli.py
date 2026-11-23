"""Command-line entry points: chat, bids migrate, eval, jobs."""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from gem_agent.config import Settings, get_settings
from gem_agent.index.db import BidIndex
from gem_agent.index.migrate import migrate_bids

log = logging.getLogger("gem_agent")

LABELLED_SET = "eval/labelled_set.json"


def setup_logging(quiet: bool) -> None:
    """Warnings only for chat (its output is the result stream); INFO for jobs. httpx logs every request at INFO."""
    if quiet:
        logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    else:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)


def labelled_path(given: str | None) -> Path:
    """The labelled set: the path given, else eval/labelled_set.json under the current directory, else the repo's."""
    if given:
        return Path(given)
    here = Path(LABELLED_SET)
    return here if here.exists() else Path(__file__).resolve().parents[1] / LABELLED_SET


def cmd_bids_migrate(args: argparse.Namespace, settings: Settings) -> int:
    from gem_agent.eval.labelled import LabelledSet

    keep = {item.b_id for item in LabelledSet.load(labelled_path(args.labelled)).items}
    source = Path(args.source) if args.source else settings.index_path
    if not source.exists():
        print(f"no old index at {source}", file=sys.stderr)
        return 2
    old = BidIndex(source)
    new = BidIndex(settings.bid_cache_path)
    try:
        copied = migrate_bids(old, new, keep)
    finally:
        old.close()
        new.close()
    print(f"copied {copied} bids to {settings.bid_cache_path}")
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


def _utf8_console() -> None:
    """Windows consoles default to cp1252; event lines use ✔/✘/→ and the eval report uses —."""
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    parser = argparse.ArgumentParser(prog="gem-agent")
    sub = parser.add_subparsers(dest="command", required=True)
    chat = sub.add_parser("chat", help="ask for tenders from the command line, results streamed")
    chat.add_argument("text")
    chat.add_argument("--user", default="cli")
    chat.add_argument("--run", help="follow up on this run id")
    serve = sub.add_parser("serve", help="the web chat (FastAPI)")
    serve.add_argument("--port", type=int)
    bids = sub.add_parser("bids", help="the bid cache")
    bids.add_argument("action", choices=["migrate"], help="migrate: copy cards and labelled bids from the old index")
    bids.add_argument("--from", dest="source", help="the old index file (default: the configured index path)")
    bids.add_argument("--labelled", help=f"labelled set whose bids are kept (default {LABELLED_SET})")
    ev = sub.add_parser("eval", help="score the Judge against the labelled set")
    ev.add_argument("--set", default=LABELLED_SET, help="labelled set file")
    ev.add_argument("--topic", help="only this topic")
    ev.add_argument("--parallel", type=int, default=3)
    ev.add_argument("--out", help="write each bid's label and verdict here (JSON)")
    bf = sub.add_parser("backfill-queries", help="add the last date for queries to older results")
    bf.add_argument("--dry-run", action="store_true", help="report what would be filled, write nothing")
    bf.add_argument("--redo-documents", action="store_true",
                    help="read again the dates taken from buyer documents (GeM pre-bid dates are kept)")
    args = parser.parse_args(argv)
    settings = get_settings()
    setup_logging(quiet=args.command == "chat")
    if args.command == "chat":
        asyncio.run(_chat(settings, args.user, args.text, args.run))
        return 0
    if args.command == "serve":
        import uvicorn

        uvicorn.run("gem_agent.app.main:app", factory=True, host=settings.app_host, port=args.port or settings.app_port)
        return 0
    if args.command == "eval":
        return asyncio.run(_eval(settings, args))
    if args.command == "backfill-queries":
        return asyncio.run(_backfill_queries(settings, args.dry_run, args.redo_documents))
    return cmd_bids_migrate(args, settings)


async def _eval(settings: Settings, args: argparse.Namespace) -> int:
    from gem_agent.app.factory import build_services
    from gem_agent.eval.labelled import LabelledSet, report, rows_json
    from gem_agent.eval.run import judge_set

    services, cache, _ = build_services(settings)
    labelled = LabelledSet.load(Path(args.set))
    rows = await judge_set(labelled, services.judge, cache, BidIndex(settings.bid_cache_path), services.collector,
                           topic=args.topic, parallel=args.parallel)
    print(report(rows))
    if args.out:
        Path(args.out).write_text(rows_json(rows), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())


async def _backfill_queries(settings: Settings, dry_run: bool, redo_documents: bool) -> int:
    from gem_agent.agents.judge import Judge
    from gem_agent.agents.models import chat_model
    from gem_agent.app.factory import judge_embedder
    from gem_agent.cache.docs import DocCache
    from gem_agent.cache.verdicts import VerdictCache
    from gem_agent.graph.store import RunStore
    from gem_agent.jobs.backfill_queries import backfill_queries

    cache, verdicts = DocCache(settings.cache_dir), VerdictCache(settings.cache_dir)
    judge = Judge(chat_model(settings), cache, verdicts, embedder=judge_embedder(settings))
    report = await backfill_queries(judge, cache, verdicts, RunStore(settings.runs_dir), dry_run=dry_run,
                                    redo_documents=redo_documents)
    for bid_number, when in sorted(report.dates.items()):
        print(f"{bid_number}  queries {when}")
    print(f"{'would fill' if dry_run else 'filled'} {report.filled} results, cleared {report.cleared}, "
          f"{report.model_calls} model calls, "
          f"{report.no_documents} bids without cached documents")
    return 0

