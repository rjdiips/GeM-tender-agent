"""Bias test, step 1: does a live search find relevant bids that the old index (and so the labelled set) never saw?

Per labelled topic, two candidate pools, both over all open bids (no date window):
- live: every page of the topic's match-term searches (fullText; exact too, which only matches ministry and
  department names), plus the vague-title category "Hiring of Agency for IT Projects- Milestone Basis". A search
  matching more than SKIP_OVER bids is not read (the ~33k department-name fullText searches) and is recorded.
- index: what the Searcher's pool takes from bids.sqlite (word matches up to 300, closest 60 by meaning, vague
  titles without a card), read-only.
Bids found by only one side are title-screened with the Searcher's own screen (vague titles are never dropped, as
in the Searcher). Up to SAMPLE plausible live-only bids per topic that are not labelled yet are sampled for labelling.

Run from the repo root: uv run --no-sync python eval/live_search/scripts/bias_pools.py <out_dir>
Writes <out_dir>/searches.jsonl (one line per live search; a rerun resumes from it), pools.json, sample.json.
"""
import asyncio
import json
import logging
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
from gem_agent.agents.models import chat_model
from gem_agent.agents.schemas import TopicDefinition
from gem_agent.agents.searcher import Searcher
from gem_agent.cli import _embedder
from gem_agent.config import get_settings
from gem_agent.index.db import BidIndex
from gem_agent.index.query import IndexQuery, search_index
from gem_agent.tools.gem.models import Bid
from gem_agent.tools.gem.search import AllBidsQuery, CategorySearch, list_all_bids, search_bids
from gem_agent.tools.gem.titles import is_vague_title
from gem_agent.tools.http_client import BidplusSession, ClientPool

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bias")
log.setLevel(logging.INFO)
OUT = Path(sys.argv[1]) if __name__ == "__main__" else Path(".")
SKIP_OVER = 3000
SAMPLE = 20
IT_CATEGORY = "services_home_itse_hi12433188"
random.seed(18)


def brief(b: Bid) -> dict:
    return {"b_id": b.b_id, "bid_number": b.bid_number, "title": b.title, "ministry": b.ministry,
            "department": b.department, "closing": b.closing.isoformat() if b.closing else None, "raw": b.raw}


async def read_all(fetch) -> dict:
    t0 = time.monotonic()
    first = await fetch(1)
    if first.num_found > SKIP_OVER:
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


def load_searches(out: Path) -> dict:
    path = out / "searches.jsonl"
    if not path.exists():
        return {}
    rows = (json.loads(line) for line in path.read_text().splitlines() if line.strip())
    return {r.pop("key"): r for r in rows}


async def live_searches(session: BidplusSession, topics: dict) -> dict:
    # One line per finished search, appended.
    path = OUT / "searches.jsonl"
    done = load_searches(OUT)
    wanted = [("category", IT_CATEGORY)] + sorted({(m, t) for d in topics.values() for t in d["match_terms"]
                                                   for m in ("fullText", "exact")})
    for mode, text in wanted:
        key = f"{mode}|{text}"
        if key in done:
            continue
        if mode == "category":
            r = await read_all(lambda n: search_bids(session, CategorySearch(category=text), n))
        else:
            r = await read_all(lambda n: list_all_bids(session, AllBidsQuery(search_text=text, search_type=mode), n))
        done[key] = r
        with path.open("a") as f:
            f.write(json.dumps({"key": key, **r}) + "\n")
        log.info("%s: %s bids%s", key, r["num_found"], " (skipped)" if r.get("skipped") else "")
    return done


def index_pool(index: BidIndex, embedder, topic: TopicDefinition) -> dict[int, Bid]:
    pool: dict[int, Bid] = {}
    for h in search_index(index, IndexQuery(terms=topic.match_terms, limit=300), embedder):
        pool[h.bid.b_id] = h.bid
    for h in search_index(index, IndexQuery(text=topic.meaning, limit=60), embedder):
        pool.setdefault(h.bid.b_id, h.bid)
    for b in index.vague_without_card():
        pool.setdefault(b.b_id, b)
    return pool


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    labelled = json.load(open("eval/labelled_set.json"))
    topics = labelled["topics"]
    already = {(i["topic"], i["b_id"]) for i in labelled["items"]}
    settings = get_settings()
    pool = ClientPool.shared(settings)
    try:
        searches = await live_searches(BidplusSession(pool), topics)
    finally:
        await pool.aclose()

    # Only read here: nothing below upserts or embeds.
    index = BidIndex(os.environ.get("BIAS_INDEX_PATH") or settings.index_path)
    embedder = _embedder(settings)
    searcher = Searcher(chat_model(settings), index, embedder)
    pools, sample = {}, {}
    for name, t in topics.items():
        topic = TopicDefinition(**t)
        live: dict[int, Bid] = {}
        via: dict[int, list[str]] = {}
        keys = [f"category|{IT_CATEGORY}"] + [f"{m}|{x}" for x in topic.match_terms for m in ("fullText", "exact")]
        for key in keys:
            for d in searches[key]["bids"]:
                live.setdefault(d["b_id"], Bid.from_listing(d["raw"]))
                via.setdefault(d["b_id"], []).append(key)
        idx = index_pool(index, embedder, topic)
        live_only = [live[b] for b in live if b not in idx]
        index_only = [idx[b] for b in idx if b not in live]
        cards = {b.b_id: c for b in live_only + index_only if is_vague_title(b.title) and (c := index.card(b.b_id))}
        screened = {}
        for side, bids in (("live_only", live_only), ("index_only", index_only)):
            to_screen = [b for b in bids if not (is_vague_title(b.title) and b.b_id not in cards)]
            kept, unscreened = await searcher._screen_titles(topic, to_screen, cards)
            screened[side] = sorted({b.b_id for b in bids if b.b_id in unscreened or b.b_id in kept
                                     or (is_vague_title(b.title) and b.b_id not in cards)})
        pools[name] = {
            "live": len(live), "index": len(idx), "both": len(set(live) & set(idx)),
            "live_only": len(live_only), "index_only": len(index_only),
            "live_only_plausible": screened["live_only"], "index_only_plausible": screened["index_only"],
            "skipped_searches": {k: searches[k]["num_found"] for k in keys if searches[k].get("skipped")},
        }
        candidates = [b for b in screened["live_only"] if (name, b) not in already]
        picked = sorted(random.sample(candidates, min(SAMPLE, len(candidates))))
        sample[name] = [{**{k: v for k, v in brief(live[b]).items() if k != "raw"}, "found_by": via[b],
                         "card": cards.get(b)} for b in picked]
        log.info("%s: live %d, index %d, both %d, live-only %d (plausible %d), index-only %d (plausible %d), "
                 "sampled %d", name, len(live), len(idx), pools[name]["both"], len(live_only),
                 len(screened["live_only"]), len(index_only), len(screened["index_only"]), len(picked))
        (OUT / "pools.json").write_text(json.dumps(pools, indent=1))
        (OUT / "sample.json").write_text(json.dumps(sample, indent=1))
    index.close()


if __name__ == "__main__":
    asyncio.run(main())
