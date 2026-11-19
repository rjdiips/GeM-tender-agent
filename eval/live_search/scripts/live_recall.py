"""Phase 2: page every live term search (0 < numFound <= CAP) to the end; record which relevant labelled bids each
finds, at which page, and the time per page. Searches whose page 1 matches an earlier one are not paged again."""
import asyncio
import json
import logging
import sys
import time

sys.path.insert(0, ".")
from gem_agent.config import get_settings
from gem_agent.tools.gem.search import AllBidsQuery, list_all_bids
from gem_agent.tools.http_client import BidplusSession, ClientPool

logging.basicConfig(level=logging.WARNING)
COUNTS, OUT = sys.argv[1], sys.argv[2]
CAP = 3000


async def main() -> None:
    labelled = json.load(open("eval/labelled_set.json"))
    relevant = {i["b_id"]: i for i in labelled["items"] if i.get("relevant")}
    counts = json.load(open(COUNTS))
    todo = sorted({(r["term"], r["mode"]) for r in counts if 0 < r["num_found"] <= CAP})
    pool = ClientPool.shared(get_settings())
    session = BidplusSession(pool)
    results, by_sig, secs = {}, {}, []
    try:
        for term, mode in todo:
            q = AllBidsQuery(search_text=term, search_type=mode)
            first = await list_all_bids(session, q, 1)
            sig = (first.num_found, tuple(b.b_id for b in first.bids))
            if sig in by_sig:
                results[f"{mode}|{term}"] = {**results[by_sig[sig]], "same_as": by_sig[sig]}
                print(f"same as {by_sig[sig]}: {term}", flush=True)
                continue
            by_sig[sig] = f"{mode}|{term}"
            found = {}  # relevant b_id -> page
            all_ids = set()
            for b in first.bids:
                all_ids.add(b.b_id)
                if b.b_id in relevant:
                    found.setdefault(b.b_id, 1)
            for n in range(2, first.page_count + 1):
                t0 = time.monotonic()
                page = await list_all_bids(session, q, n)
                secs.append(time.monotonic() - t0)
                for b in page.bids:
                    all_ids.add(b.b_id)
                    if b.b_id in relevant:
                        found.setdefault(b.b_id, n)
                if page.empty_confirmed:
                    break
            results[f"{mode}|{term}"] = {"num_found": first.num_found, "pages": first.page_count,
                                         "unique": len(all_ids), "found": found}
            print(f"{first.num_found:5} {first.page_count:4}p rel={len(found)}  {mode} {term}", flush=True)
            json.dump({"results": results, "secs": secs}, open(OUT, "w"), indent=1)
    finally:
        await pool.aclose()
        json.dump({"results": results, "secs": secs}, open(OUT, "w"), indent=1)


asyncio.run(main())
