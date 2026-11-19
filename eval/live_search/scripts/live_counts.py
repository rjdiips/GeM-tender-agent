"""Phase 1: numFound (page 1 only) of every labelled topic's match terms, fullText and exact, open bids only."""
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
OUT = sys.argv[1]


async def main() -> None:
    topics = json.load(open("eval/labelled_set.json"))["topics"]
    pool = ClientPool.shared(get_settings())
    session = BidplusSession(pool)
    rows = []
    try:
        for topic, t in topics.items():
            for term in t["match_terms"]:
                for mode in ("fullText", "exact"):
                    t0 = time.monotonic()
                    page = await list_all_bids(session, AllBidsQuery(search_text=term, search_type=mode), 1)
                    rows.append({"topic": topic, "term": term, "mode": mode, "num_found": page.num_found,
                                 "secs": round(time.monotonic() - t0, 2)})
                    print(f"{topic:8} {mode:8} {page.num_found:6}  {term}", flush=True)
    finally:
        await pool.aclose()
        json.dump(rows, open(OUT, "w"), indent=1)


asyncio.run(main())
