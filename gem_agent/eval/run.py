"""Judge every labelled bid with the current code into a scratch verdict store, so stored verdicts are untouched and
nothing cached hides a change in the Judge."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from gem_agent.agents.collector import Collector
from gem_agent.agents.judge import Judge
from gem_agent.cache.docs import DocCache
from gem_agent.cache.verdicts import VerdictCache
from gem_agent.eval.labelled import LabelledSet, Row
from gem_agent.index.db import BidIndex


async def judge_set(labelled: LabelledSet, judge: Judge, cache: DocCache, bid_cache: BidIndex,
                    collector: Collector | None = None, *, topic: str | None = None, parallel: int = 3,
                    scratch: Path | None = None) -> list[Row]:
    judge.verdicts = VerdictCache(scratch or Path(tempfile.mkdtemp(prefix="gem-eval-")))
    items = [i for i in labelled.items if topic is None or i.topic == topic]
    slots = asyncio.Semaphore(parallel)

    async def one(item) -> Row:
        async with slots:
            bid = bid_cache.get(item.b_id)
            files = cache.load(item.b_id)
            if files is None and collector is not None and bid is not None:
                files = await collector.collect(bid)
            if bid is None or files is None:
                return Row(label=item, error="bid or its documents are not available")
            try:
                return Row(label=item, verdict=await judge.judge(labelled.topics[item.topic], bid, files))
            except Exception as e:  # noqa: BLE001 - one failed bid must not stop the evaluation
                return Row(label=item, error=f"{type(e).__name__}: {e}")

    return list(await asyncio.gather(*(one(i) for i in items)))
