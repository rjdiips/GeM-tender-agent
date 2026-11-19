"""Bias test, step 2: fetch the sampled bids' documents (into the shared document cache, which the Judge reuses) and
write one text bundle per bid for labelling, in the same shape as eval/labelling/select_bids.py: the bid document's
first pages and up to two RFPs' scope sections.

Waits while the disk is struggling: /proc/pressure/io "some" avg10 above MAX_IO_PRESSURE (default 20%, the
shared-server notice's line; set BIAS_MAX_IO_PRESSURE to change it).

Run from the repo root: uv run --no-sync python eval/live_search/scripts/bias_bundle.py <out_dir>
Reads <out_dir>/sample.json (or the sample file given as a second argument); writes <out_dir>/bids/<b_id>.txt and <out_dir>/bundles.json.
"""
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, ".")
from gem_agent.agents.collector import Collector
from gem_agent.cache.docs import DocCache
from gem_agent.config import get_settings
from gem_agent.index.cards import _rfp_sections  # its scope-heading regex was fixed on 18 Nov (spec 14.2)
from gem_agent.tools.gem.models import Bid
from gem_agent.tools.http_client import BidplusSession, ClientPool

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bundle")
log.setLevel(logging.INFO)
OUT = Path(sys.argv[1])
# The notice's line is 20%. On 18 Nov the shared disk stayed at 30-60% all evening, so that run used 50%
# (BIAS_MAX_IO_PRESSURE=50) to finish the same night.
MAX_IO_PRESSURE = float(os.environ.get("BIAS_MAX_IO_PRESSURE", "20"))


def io_pressure() -> float:
    line = Path("/proc/pressure/io").read_text().splitlines()[0]
    return float(dict(p.split("=") for p in line.split()[1:])["avg10"])


async def wait_for_disk() -> None:
    while (p := io_pressure()) > MAX_IO_PRESSURE:
        log.info("io pressure %.1f%% > %.0f%%: waiting", p, MAX_IO_PRESSURE)
        await asyncio.sleep(30)


class CacheOnly:
    def __init__(self, cache: DocCache) -> None:
        self.cache = cache


def bundle(cache: DocCache, item: dict) -> str | None:
    f = cache.load(item["b_id"])
    if not f:
        return None
    docs = [r for r in f.files if r.role in ("bid_document", "parent_document") and r.status == "ok"]
    body = "\n".join(p.text for r in docs for p in cache.load_text(r)[:3])[:6000]
    rfp = _rfp_sections(CacheOnly(cache), f, body, 2) if docs else ""
    return (f"# {item['bid_number']} — {item['title']}\nBuyer: {item['ministry'] or ''} / {item['department'] or ''}\n"
            f"Card: {item.get('card')}\n\n## Bid document\n{body}\n\n## RFP\n{rfp}\n")


async def main() -> None:
    settings = get_settings()
    sample = json.loads(Path(sys.argv[2]).read_text() if len(sys.argv) > 2 else (OUT / "sample.json").read_text())
    path = OUT / "searches.jsonl"  # listing data; an item can also carry its own ("raw"), e.g. index-only bids
    searches = [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
    raw = {d["b_id"]: d["raw"] for s in searches for d in s["bids"]}
    (OUT / "bids").mkdir(exist_ok=True)
    cache = DocCache(settings.cache_dir)
    pool = ClientPool.shared(settings)
    collector = Collector(BidplusSession(pool), cache)
    done: dict[str, dict] = {}
    try:
        items = {i["b_id"]: i for items in sample.values() for i in items}
        for n, (b_id, item) in enumerate(sorted(items.items()), 1):
            path = OUT / "bids" / f"{b_id}.txt"
            if not path.exists():
                await wait_for_disk()
                try:
                    await collector.collect(Bid.from_listing(item.get("raw") or raw[b_id]), attachments=True)
                except Exception as exc:  # noqa: BLE001 - one bad bid must not stop the rest
                    log.warning("%s: collect failed: %s", b_id, exc)
                text = bundle(cache, item)
                if text is None:
                    done[str(b_id)] = {"ok": False}
                    continue
                path.write_text(text)
            done[str(b_id)] = {"ok": True, "file": str(path)}
            log.info("%d/%d %s", n, len(items), b_id)
    finally:
        await pool.aclose()
        (OUT / "bundles.json").write_text(json.dumps(done, indent=1))


asyncio.run(main())
