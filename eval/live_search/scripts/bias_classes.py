"""Untested tender classes, phase A: what the live-search design and the old index each find, before any labelling.

For each class phrase, the app's own Chat agent writes the topic definition and the Planner the search plan (with a
scratch knowledge dir, so no user memory or saved topic meanings change). Then, from that plan:
- live pool, as the live-search design would run it: categories (the plan's, plus "Hiring of Agency for IT
  Projects"), buyers in exact mode (the plan's ministries and departments, plus Department of Financial Services),
  then the plan's terms in fullText. Every search is read to the end, except a fullText search matching more than
  SKIP_OVER bids (near the whole list). Searches whose page 1 equals an earlier one are not read again.
- index pool: the Searcher's pool from bids.sqlite for the same plan (word matches, closest by meaning, vague titles
  without a card, and the plan's categories as the Searcher reads them: at most 20 pages each). No date window.
Every bid in either pool is title-screened with the Searcher's screen (vague titles without a card are kept).

Run from the repo root: uv run --no-sync python eval/live_search/scripts/bias_classes.py <out_dir>
Env: BIAS_INDEX_PATH (a copy of bids.sqlite, e.g. in /dev/shm). Writes <out_dir>/plans.json, searches.jsonl (one
line per live search, resumable), classes.json.
"""
import asyncio
import functools
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, str(Path(__file__).parent))
from bias_pools import brief, index_pool  # noqa: E402

from gem_agent.agents.chat import ChatAgent
from gem_agent.agents.models import chat_model
from gem_agent.agents.planner import Planner
from gem_agent.agents.searcher import Searcher
from gem_agent.cache.lookups import LookupCatalog
from gem_agent.cli import _embedder
from gem_agent.config import get_settings
from gem_agent.index.db import BidIndex
from gem_agent.index.probe import probe_site
from gem_agent.memory.topics import TopicStore
from gem_agent.memory.user_memory import MemoryStore
from gem_agent.tools.gem.models import Bid, site_now
from gem_agent.tools.gem.search import AllBidsQuery, CategorySearch, list_all_bids, search_bids
from gem_agent.tools.gem.titles import is_vague_title
from gem_agent.tools.http_client import BidplusSession, ClientPool

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("classes")
log.setLevel(logging.INFO)
OUT = Path(sys.argv[1])
SKIP_OVER = 20000
IT_CATEGORY = "services_home_itse_hi12433188"
BASE_DEPARTMENTS = ["Department of Financial Services"]
SEARCHER_CATEGORY_PAGES = 20
CLASSES = {
    "treasury": "Treasury management and Murex tenders",
    "core_banking": "Core banking system tenders",
    "payments": "Payment systems tenders",
    "cib": "Corporate internet banking tenders",
    "mna": "Merger and acquisition advisory tenders",
}


def load_searches() -> dict:
    path = OUT / "searches.jsonl"
    if not path.exists():
        return {}
    rows = (json.loads(line) for line in path.read_text().splitlines() if line.strip())
    return {r.pop("key"): r for r in rows}


async def run_search(session, key: str, fetch, signatures: dict) -> dict:
    t0 = time.monotonic()
    first = await fetch(1)
    sig = f"{first.num_found}|" + ",".join(str(b.b_id) for b in first.bids)
    if first.num_found and sig in signatures:
        return {"num_found": first.num_found, "same_as": signatures[sig], "bids": []}
    signatures[sig] = key
    if key.startswith("fullText|") and first.num_found > SKIP_OVER:
        return {"num_found": first.num_found, "skipped": True, "bids": []}
    bids = {b.b_id: b for b in first.bids}
    for n in range(2, first.page_count + 1):
        page = await fetch(n)
        for b in page.bids:
            bids.setdefault(b.b_id, b)
        if page.empty_confirmed:
            break
    return {"num_found": first.num_found, "pages": first.page_count, "secs": round(time.monotonic() - t0, 1),
            "bids": [brief(b) for b in bids.values()]}


def search_keys(plan) -> list[str]:
    cats = list(dict.fromkeys([*plan.categories, IT_CATEGORY]))
    buyers = list(dict.fromkeys([*plan.ministries, *plan.prefer_ministries, *plan.departments,
                                 *plan.prefer_departments, *BASE_DEPARTMENTS]))
    return ([f"category|{c}" for c in cats] + [f"exact|{b}" for b in buyers]
            + [f"fullText|{t}" for t in dict.fromkeys(plan.terms)])


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    index = BidIndex(os.environ.get("BIAS_INDEX_PATH") or settings.index_path)  # only read
    embedder = _embedder(settings)
    model = chat_model(settings)
    pool = ClientPool.shared(settings)
    session = BidplusSession(pool)
    try:
        catalog = LookupCatalog(settings.cache_dir, index)
        if not catalog.is_fresh(site_now().date()):
            await catalog.refresh(session, today=site_now().date())
        scratch = Path(tempfile.mkdtemp(prefix="bias-classes-"))
        chat = ChatAgent(model, probe=functools.partial(probe_site, index, embedder),
                         memory=MemoryStore(scratch), topics=TopicStore(scratch))
        planner = Planner(model, catalog)

        plans_path = OUT / "plans.json"
        plans = json.loads(plans_path.read_text()) if plans_path.exists() else {}
        for key, text in CLASSES.items():
            if key in plans:
                continue
            u = await chat.understand("bias-test", text, [], run_id=f"bias-{key}")
            topic = u.request.topics[0]
            plan = await planner.plan(u.request, topic)
            plans[key] = {"text": text, "topic": topic.model_dump(mode="json"), "plan": plan.model_dump(mode="json")}
            plans_path.write_text(json.dumps(plans, indent=1))
            log.info("%s: terms %s | categories %s | buyers %s", key, plan.terms, plan.categories,
                     plan.ministries + plan.departments + plan.prefer_ministries + plan.prefer_departments)

        from gem_agent.agents.schemas import SearchPlan, TopicDefinition
        searches = load_searches()
        signatures = {f"{v['num_found']}|" + ",".join(str(b["b_id"]) for b in v["bids"][:10]): k
                      for k, v in searches.items() if v.get("bids")}
        for key in plans:
            for skey in search_keys(SearchPlan(**plans[key]["plan"])):
                if skey in searches:
                    continue
                mode, value = skey.split("|", 1)
                if mode == "category":
                    fetch = lambda n, v=value: search_bids(session, CategorySearch(category=v), n)  # noqa: E731
                else:
                    fetch = lambda n, v=value, m=mode: list_all_bids(  # noqa: E731
                        session, AllBidsQuery(search_text=v, search_type=m), n)
                r = await run_search(session, skey, fetch, signatures)
                searches[skey] = r
                with (OUT / "searches.jsonl").open("a") as f:
                    f.write(json.dumps({"key": skey, **r}) + "\n")
                log.info("%s: %s bids%s", skey, r["num_found"],
                         " (skipped)" if r.get("skipped") else f" (same as {r['same_as']})" if r.get("same_as") else
                         f", {r.get('pages')} pages, {r.get('secs')} s")
    finally:
        await pool.aclose()

    searcher = Searcher(model, index, embedder)
    results = {}
    for key, p in plans.items():
        topic, plan = TopicDefinition(**p["topic"]), SearchPlan(**p["plan"])
        live: dict[int, Bid] = {}
        via: dict[int, list[str]] = {}
        secs, read = 0.0, set()
        for skey in search_keys(plan):
            src = searches[skey].get("same_as") or skey
            r = searches[src]
            if src not in read:  # a search that repeats one already read costs one page, not a second read
                read.add(src)
                secs += r.get("secs", 0.0)
            for d in r["bids"]:
                live.setdefault(d["b_id"], Bid.from_listing(d["raw"]))
                via.setdefault(d["b_id"], []).append(skey)
        idx = index_pool(index, embedder, topic.model_copy(update={"match_terms": plan.terms, "meaning": plan.text}))
        for c in plan.categories:  # the Searcher's own live category reads, at most 20 pages each
            for d in searches[f"category|{c}"]["bids"][: SEARCHER_CATEGORY_PAGES * 10]:
                idx.setdefault(d["b_id"], Bid.from_listing(d["raw"]))
        everything = {**idx, **live}
        cards = {b: c for b, bid in everything.items() if is_vague_title(bid.title) and (c := index.card(b))}
        to_screen = [bid for b, bid in everything.items() if not (is_vague_title(bid.title) and b not in cards)]
        kept = await searcher._screen_titles(topic, to_screen, cards)
        plausible = {b for b, bid in everything.items()
                     if kept is None or b in kept or (is_vague_title(bid.title) and b not in cards)}
        groups = {"both": sorted(plausible & set(live) & set(idx)),
                  "live_only": sorted(plausible & (set(live) - set(idx))),
                  "index_only": sorted(plausible & (set(idx) - set(live)))}
        results[key] = {"live": len(live), "index": len(idx), "live_search_secs": round(secs),
                        "plausible": {g: len(v) for g, v in groups.items()}, "groups": groups,
                        "found_by": {str(b): via.get(b, []) for b in plausible},
                        "titles": {str(b): everything[b].title for b in plausible}}
        log.info("%s: live %d, index %d, live-search time %d s, plausible both %d / live-only %d / index-only %d",
                 key, len(live), len(idx), secs, *(len(v) for v in groups.values()))
        (OUT / "classes.json").write_text(json.dumps(results, indent=1, ensure_ascii=False))
    index.close()


if __name__ == "__main__":
    asyncio.run(main())
