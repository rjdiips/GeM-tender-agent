"""Spike (throwaway): describe each labelled bid once, then compare three ways of deciding which bids get a full read.

today: the title screen (with bid cards for vague titles, as the Searcher does), then triage on the first pages.
A:     the model matches the topic against batches of descriptions (match / unsure / no); "no" drops a bid only when
       its description was written from a scope document or RFP, otherwise the bid is read.
B:     cosine similarity of topic and description (all-MiniLM-L6-v2): how many non-matches must be read to keep
       every real match.

Only cached documents are read: no GeM requests. Descriptions are cached in descriptions.jsonl so a rerun is cheap.
Run: uv run python eval/describe_ab/run.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from pydantic import BaseModel, Field
from pydantic_ai import Agent, NativeOutput

from gem_agent.agents.judge import Judge
from gem_agent.agents.models import chat_model
from gem_agent.agents.searcher import Searcher
from gem_agent.cache.docs import DocCache
from gem_agent.cache.verdicts import VerdictCache
from gem_agent.config import get_settings
from gem_agent.eval.labelled import LabelledSet
from gem_agent.index.cards import CardOutput, _rfp_sections
from gem_agent.index.db import BidIndex
from gem_agent.retrieval.embed import SmallEmbedder
from gem_agent.tools.gem.models import Bid
from gem_agent.tools.gem.titles import is_vague_title

HERE = Path(__file__).parent
LABELS: dict = {}  # b_id -> Label, for bids the bid cache no longer holds


def get_bid(bid_cache, b_id: int) -> Bid | None:
    """The bid cache's row, else a minimal Bid from the label (no buyer, no dates): 71 labelled bids were pruned."""
    if (b := bid_cache.get(b_id)) is not None:
        return b
    lab = LABELS.get(b_id)
    if lab is None:
        return None
    return Bid(b_id=b_id, bid_number=lab.bid_number, title=lab.title, bid_type=2 if "/R/" in lab.bid_number else 1)
DESC_FILE = HERE / "descriptions.jsonl"
PARALLEL = 3

DESCRIBE = """You say what an Indian government (GeM) tender is buying, from its bid document and RFP.
GeM titles and the "Item Category" are often only a generic category ("Hiring of Agency for IT Projects- Milestone
basis", "Custom Bid for Services"): look past them to the buyer-added terms, the scope of work, the RFP and its file
names.
subject: name what is bought, and for whom if stated. One line, no preamble.
terms: up to 8 specific systems, modules, functions or goods in scope, as the documents name them. Never category or
procurement words such as "IT projects" or "software".
If the text does not say what is being bought, answer with an empty subject and no terms."""

MATCH = """You decide, for one topic, which Indian government (GeM) tenders could be buying it, from a short
description of what each tender buys.
match: the topic itself is plainly in scope.
unsure: it might be: a broad platform or programme that could include the topic, a related-but-not-enough item that
may come with it, or a description too thin to tell.
Everything else is no: plainly about something else. Look-alikes listed under "NOT this" are no.
Answer only with the bracketed numbers: no explanations."""


class MatchOut(BaseModel):
    match: list[int] = Field(description="bracketed numbers that match")
    unsure: list[int] = Field(description="bracketed numbers that might")


async def describe_all(labelled, bid_cache, cache, model) -> dict[int, dict]:
    done = {}
    if DESC_FILE.exists():
        for line in DESC_FILE.read_text(encoding="utf-8").splitlines():
            d = json.loads(line)
            done[d["b_id"]] = d
    agent = Agent(model, output_type=NativeOutput(CardOutput), instructions=DESCRIBE, retries=2)
    collector = SimpleNamespace(cache=cache)
    slots = asyncio.Semaphore(PARALLEL)
    todo = sorted({i.b_id for i in labelled.items} - set(done))

    async def one(b_id: int) -> None:
        async with slots:
            bid, files = get_bid(bid_cache, b_id), cache.load(b_id)
            if bid is None or files is None:
                done[b_id] = {"b_id": b_id, "error": "missing"}
                return
            docs = [f for f in files.files if f.role in ("bid_document", "parent_document") and f.status == "ok"]
            bid_text = "\n".join(p.text for d in docs for p in cache.load_text(d))[:12000]
            sections = _rfp_sections(collector, files, bid_text, 3)
            prompt = (f"Title: {bid.title}\nBuyer: {bid.ministry or ''} / {bid.department or ''}\n\n"
                      f"=== Bid document ===\n{bid_text or '(no readable text)'}" + (f"\n\n{sections}" if sections else ""))
            t0 = time.monotonic()
            try:
                out = (await agent.run(prompt, model_settings={"max_tokens": 400, "temperature": 0.0})).output
                rec = {"b_id": b_id, "subject": out.subject, "terms": out.terms, "has_scope": bool(sections),
                       "seconds": round(time.monotonic() - t0, 2), "prompt_chars": len(prompt)}
            except Exception as exc:  # noqa: BLE001
                rec = {"b_id": b_id, "error": str(exc)[:200], "seconds": round(time.monotonic() - t0, 2)}
            done[b_id] = rec
            with DESC_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            print(f"described {len(done)}/{len(done) + len(todo) - 1}", end="\r", file=sys.stderr)

    await asyncio.gather(*(one(b) for b in todo))
    return done


def desc_text(d: dict) -> str:
    if not d or d.get("error") or not d.get("subject"):
        return ""
    return d["subject"] + ("; " + "; ".join(d["terms"]) if d.get("terms") else "")


async def way_today(labelled, bid_cache, cache, model, items_by_topic) -> tuple[dict, dict]:
    """(b_id, topic) -> read fully?, and call counts/time."""
    searcher = Searcher(model, bid_cache, live=None)  # type: ignore[arg-type]
    judge = Judge(model, cache, VerdictCache(Path(tempfile.mkdtemp(prefix="describe-ab-"))))
    judge.settings.parallel = PARALLEL
    judge._slots = asyncio.Semaphore(PARALLEL)
    read: dict = {}
    stats = {"screen_calls": 0, "triage_calls": 0, "seconds": 0.0}
    t0 = time.monotonic()
    for topic_key, items in items_by_topic.items():
        topic = labelled.topics[topic_key]
        bids = [b for i in items if (b := get_bid(bid_cache, i.b_id))]
        cards = {b.b_id: c for b in bids if is_vague_title(b.title) and (c := bid_cache.card(b.b_id))}
        vague = {b.b_id for b in bids if is_vague_title(b.title) and b.b_id not in cards}
        screened = [b for b in bids if b.b_id not in vague]
        kept = await searcher._screen_titles(topic, screened, cards)
        stats["screen_calls"] += -(-len(screened) // searcher.settings.screen_batch)
        passed = [b for b in bids if b.b_id in vague or kept is None or b.b_id in kept]

        async def tri(b):
            files = cache.load(b.b_id)
            if files is None:
                return b.b_id, True
            verdict, _ = await judge.screen(topic, b, files)
            return b.b_id, verdict is None  # a verdict here is triage's clear no
        stats["triage_calls"] += len(passed)
        results = dict(await asyncio.gather(*(tri(b) for b in passed)))
        for b in bids:
            read[(b.b_id, topic_key)] = results.get(b.b_id, False)
    stats["seconds"] = round(time.monotonic() - t0, 1)
    return read, stats


async def way_a(labelled, model, items_by_topic, descs) -> tuple[dict, dict, dict]:
    agent = Agent(model, output_type=NativeOutput(MatchOut), instructions=MATCH, retries=2)
    read, call = {}, {}
    stats = {"match_calls": 0, "seconds": 0.0}
    t0 = time.monotonic()
    for topic_key, items in items_by_topic.items():
        topic = labelled.topics[topic_key]
        for start in range(0, len(items), 40):
            batch = items[start:start + 40]
            listing = "\n".join(f"[{n}] {desc_text(descs.get(i.b_id)) or '(no description: ' + i.title[:150] + ')'}"
                                for n, i in enumerate(batch))
            out = (await agent.run(f"{topic.render()}\n\nTenders:\n{listing}",
                                   model_settings={"max_tokens": 600, "temperature": 0.0})).output
            stats["match_calls"] += 1
            for n, i in enumerate(batch):
                c = "match" if n in out.match else "unsure" if n in out.unsure else "no"
                d = descs.get(i.b_id) or {}
                strong = bool(desc_text(d)) and d.get("has_scope")
                call[(i.b_id, topic_key)] = c
                read[(i.b_id, topic_key)] = c != "no" or not strong
    stats["seconds"] = round(time.monotonic() - t0, 1)
    return read, call, stats


def way_b(labelled, items_by_topic, descs) -> dict:
    emb = SmallEmbedder(get_settings().judge_cpu_embed_model)
    out = {}
    for topic_key, items in items_by_topic.items():
        topic = labelled.topics[topic_key]
        q = topic.meaning + "; " + "; ".join(topic.match_terms)
        texts = [desc_text(descs.get(i.b_id)) or i.title for i in items]
        v = emb.encode([q, *texts])
        sims = v[1:] @ v[0]
        order = np.argsort(-sims)
        ranked = [items[j] for j in order]
        pos = [r for r, i in enumerate(ranked) if i.relevant]
        out[topic_key] = {"n": len(items), "positives": len(pos),
                          "read_to_keep_all": (max(pos) + 1) if pos else 0,
                          "positive_ranks": [p + 1 for p in pos]}
    return out


def score(name: str, read: dict, items, *, exclude_review=False) -> dict:
    rows = [i for i in items if not (exclude_review and i.to_review and not i.reviewed_by)]
    pos = [i for i in rows if i.relevant]
    neg = [i for i in rows if not i.relevant]
    missed = [i for i in pos if not read.get((i.b_id, i.topic), True)]
    return {"way": name, "labels": "reviewed+clear" if exclude_review else "all", "positives": len(pos),
            "missed": len(missed), "missed_items": [f"{i.topic}:{i.b_id}:{i.title[:50]}" for i in missed],
            "negatives": len(neg), "negatives_read": sum(read.get((i.b_id, i.topic), True) for i in neg)}


async def main() -> None:
    s = get_settings()
    labelled = LabelledSet.load(Path("eval/labelled_set.json"))
    bid_cache, cache, model = BidIndex(s.bid_cache_path), DocCache(s.cache_dir), chat_model(s)
    LABELS.update({i.b_id: i for i in labelled.items})
    items_by_topic: dict = {}
    for i in labelled.items:
        items_by_topic.setdefault(i.topic, []).append(i)

    t0 = time.monotonic()
    descs = await describe_all(labelled, bid_cache, cache, model)
    desc_seconds = round(time.monotonic() - t0, 1)
    ok = [d for d in descs.values() if not d.get("error")]
    print(f"\ndescriptions: {len(ok)} ok, {len(descs) - len(ok)} failed, "
          f"{sum(1 for d in ok if d.get('has_scope'))} with a scope/RFP section, "
          f"{sum(1 for d in ok if not d.get('subject'))} empty; wall {desc_seconds}s", file=sys.stderr)

    today, today_stats = await way_today(labelled, bid_cache, cache, model, items_by_topic)
    a, a_calls, a_stats = await way_a(labelled, model, items_by_topic, descs)
    b = way_b(labelled, items_by_topic, descs)

    report = {
        "descriptions": {"ok": len(ok), "failed": len(descs) - len(ok),
                         "with_scope": sum(1 for d in ok if d.get("has_scope")),
                         "empty": sum(1 for d in ok if not d.get("subject")),
                         "median_seconds": float(np.median([d["seconds"] for d in ok if "seconds" in d] or [0])),
                         "wall_seconds_this_run": desc_seconds},
        "today": [score("today", today, labelled.items), score("today", today, labelled.items, exclude_review=True)],
        "today_stats": today_stats,
        "A": [score("A", a, labelled.items), score("A", a, labelled.items, exclude_review=True)],
        "A_strict_no_drops_all": [score("A-strict", {k: v != "no" for k, v in a_calls.items()}, labelled.items)],
        "A_stats": a_stats,
        "A_calls": {c: sum(1 for v in a_calls.values() if v == c) for c in ("match", "unsure", "no")},
        "B": b,
        "B_total": {"read_to_keep_all": sum(t["read_to_keep_all"] for t in b.values()),
                    "items": sum(t["n"] for t in b.values())},
    }
    with (HERE / "decisions.jsonl").open("w", encoding="utf-8") as f:
        for i in labelled.items:
            k = (i.b_id, i.topic)
            f.write(json.dumps({"topic": i.topic, "b_id": i.b_id, "relevant": i.relevant, "unsure": i.unsure,
                                "clear": not (i.to_review and not i.reviewed_by), "today": today.get(k, True),
                                "A": a.get(k, True), "A_call": a_calls.get(k)}) + "\n")
    (HERE / "results.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
