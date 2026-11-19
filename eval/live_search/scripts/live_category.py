"""Category search for vague-title bids: do the vague categories, searched live, reach every relevant vague bid, and
every open vague bid the local index knows? How many pages and seconds does it take?"""
import asyncio
import json
import logging
import sqlite3
import sys
import time

sys.path.insert(0, ".")
from gem_agent.config import get_settings
from gem_agent.tools.gem.lookups import list_categories
from gem_agent.tools.gem.search import AllBidsQuery, CategorySearch, list_all_bids, search_bids
from gem_agent.tools.gem.titles import is_vague_title
from gem_agent.tools.http_client import BidplusSession, ClientPool

logging.basicConfig(level=logging.WARNING)
OUT = sys.argv[1]


async def page_all(fetch):
    t0, ids, titles = time.monotonic(), [], {}
    first = await fetch(1)
    pages = [first]
    for n in range(2, first.page_count + 1):
        pages.append(await fetch(n))
    for p in pages:
        for b in p.bids:
            if b.b_id not in titles:
                ids.append(b.b_id)
                titles[b.b_id] = b.title
    return {"num_found": first.num_found, "pages": first.page_count, "secs": round(time.monotonic() - t0, 1),
            "ids": ids, "titles": titles}


async def main() -> None:
    labelled = json.load(open("eval/labelled_set.json"))
    rel_vague = {i["b_id"]: i["topic"] for i in labelled["items"] if i.get("relevant") and is_vague_title(i["title"])}
    db = sqlite3.connect("file:data/index/bids.sqlite?mode=ro", uri=True, timeout=30)
    idx_vague = {b for b, t in db.execute("SELECT b_id, title FROM bids WHERE state='open'") if t and is_vague_title(t)}
    pool = ClientPool.shared(get_settings())
    s = BidplusSession(pool)
    out = {"relevant_vague": rel_vague, "index_open_vague": len(idx_vague)}
    try:
        cats = await list_categories(s)
        vague_cats = [c for c in cats if "hiring of agency for it projects" in c.label.lower()
                      or c.label.lower().startswith("custom bid for services")]
        out["vague_categories"] = [(c.value, c.label) for c in vague_cats]
        print("vague categories:", out["vague_categories"], flush=True)
        found_all = set()
        for c in vague_cats:
            r = await page_all(lambda n, c=c: search_bids(s, CategorySearch(category=c.value), n))
            found_all |= set(r["ids"])
            out[f"category|{c.label}"] = {k: v for k, v in r.items() if k != "titles"} | {
                "vague": sum(1 for t in r["titles"].values() if is_vague_title(t))}
            print(f"category {c.label}: {r['num_found']} bids, {r['pages']}p, {r['secs']}s", flush=True)
        for text in ("Hiring of Agency for IT Projects", "Custom Bid for Services"):
            r = await page_all(lambda n, text=text: list_all_bids(s, AllBidsQuery(search_text=text), n))
            out[f"fullText|{text}"] = {k: v for k, v in r.items() if k not in ("titles", "ids")} | {
                "vague": sum(1 for t in r["titles"].values() if is_vague_title(t))}
            found_all |= {b for b, t in r["titles"].items() if is_vague_title(t)}
            print(f"fullText {text}: {r['num_found']} bids, {r['pages']}p, {r['secs']}s", flush=True)
        out["relevant_vague_reached"] = {b: (b in found_all) for b in rel_vague}
        out["index_vague_reached"] = len(idx_vague & found_all)
        out["index_vague_missed_titles"] = [t for b, t in db.execute("SELECT b_id, title FROM bids WHERE state='open'")
                                            if b in idx_vague - found_all][:40]
    finally:
        await pool.aclose()
        json.dump(out, open(OUT, "w"), indent=1, default=list)
    print(json.dumps({k: v for k, v in out.items() if not k.startswith(("category|", "fullText|"))}, indent=1,
                     default=list)[:4000])


asyncio.run(main())
