"""Repeatable A/B evaluation of description matching, using cached documents only.

Three ways are scored on the labelled set: today's title and first-pages path, A (the
description matcher with the safety net off), and today-or-A (the matcher with the
safety net on). Buyer topics are reported separately because a description cannot
decide them alone. Nothing here constructs a collector or a live GeM searcher.
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from gem_agent.agents.judge import Judge, doc_hash
from gem_agent.agents.matcher import Call, Matcher, MatchItem, buyer_line
from gem_agent.agents.schemas import TopicDefinition
from gem_agent.agents.searcher import Candidate, Searcher
from gem_agent.cache.docs import BidFiles, DocCache
from gem_agent.cache.verdicts import VerdictCache
from gem_agent.eval.labelled import Label, LabelledSet
from gem_agent.graph.deciding import route
from gem_agent.index.db import BidIndex, Description
from gem_agent.index.describe import Describer
from gem_agent.tools.gem.models import Bid
from gem_agent.tools.gem.titles import is_vague_title

log = logging.getLogger(__name__)

BUYER_TOPICS = frozenset({"mof"})
Way = Literal["today", "A", "today or A"]
Topics = Literal["subject", "buyer"]


def checked(label: Label) -> bool:
    return not (label.to_review and not label.reviewed_by)


class WayScore(BaseModel):
    way: Way
    topics: Topics
    run: int
    checked_missed: int
    all_missed: int
    negatives_read: int
    negatives: int
    model_calls: int
    missed: list[str]  # "topic:b_id: description or title"


class MatchReport(BaseModel):
    scores: list[WayScore]
    retire: bool


def _bid_from_label(label: Label) -> Bid:
    """A pruned cache row rebuilt only from the label, as the original spike did."""
    return Bid(b_id=label.b_id, bid_number=label.bid_number, title=label.title,
               bid_type=2 if "/R/" in label.bid_number else 1)


def _bid(label: Label, index: BidIndex) -> Bid:
    return index.get(label.b_id) or _bid_from_label(label)


def _load_files(cache: DocCache, b_id: int) -> BidFiles | None:
    try:
        return cache.load(b_id)
    except Exception as exc:  # noqa: BLE001 - one unreadable manifest must not stop the evaluation
        log.warning("cached documents for %s could not be read: %s", b_id, exc)
        return None


async def describe_labelled(labelled: LabelledSet, describer: Describer, index: BidIndex,
                            cache: DocCache) -> dict[int, Description]:
    """Reuse current descriptions and write the rest from cached documents only.

    A missing manifest leaves the bid undescribed, even if an old description row is
    still in SQLite. A current row must also match the cached documents' hash.
    """
    labels = {item.b_id: item for item in labelled.items}
    known = describer.known(labels)
    semaphore = asyncio.Semaphore(3)

    async def one(b_id: int) -> Description | None:
        async with semaphore:
            files = _load_files(cache, b_id)
            if files is None:
                return None
            row = known.get(b_id)
            if row is not None and row.current(describer.model_name) and row.doc_hash == doc_hash(files):
                return row
            try:
                return await describer.describe(_bid(labels[b_id], index), files)
            except Exception as exc:  # noqa: BLE001 - one failed bid must not stop the evaluation
                log.warning("description for %s failed: %s", b_id, exc)
                return None

    rows = await asyncio.gather(*(one(b_id) for b_id in sorted(labels)))
    return {b_id: row for b_id, row in zip(sorted(labels), rows, strict=True) if row is not None}


def _items_for(topics: Topics, items: list[Label]) -> list[Label]:
    return [item for item in items if (item.topic in BUYER_TOPICS) == (topics == "buyer")]


def score_way(way: Way, reads: dict[tuple[int, str], bool], labelled: LabelledSet, *, topics: Topics,
              run: int, model_calls: int, descriptions: dict[int, Description]) -> WayScore:
    """Score one way for one topic class and run; an unvisited key is conservatively read."""
    items = _items_for(topics, labelled.items)
    missed: list[str] = []
    for item in items:
        if not item.relevant or reads.get((item.b_id, item.topic), True):
            continue
        row = descriptions.get(item.b_id)
        detail = row.text() if row is not None and row.usable() else item.title
        missed.append(f"{item.topic}:{item.b_id}: {detail}")
    negatives = [item for item in items if not item.relevant]
    return WayScore(
        way=way,
        topics=topics,
        run=run,
        checked_missed=sum(checked(item) for item in items
                           if item.relevant and not reads.get((item.b_id, item.topic), True)),
        all_missed=len(missed),
        negatives_read=sum(reads.get((item.b_id, item.topic), True) for item in negatives),
        negatives=len(negatives),
        model_calls=model_calls,
        missed=missed,
    )


def _candidate(topic_key: str, bid: Bid, *, kept: bool) -> Candidate:
    return Candidate(bid=bid, group=topic_key, why=[] if kept else ["title screen: no"])


async def _today_reads(items: list[Label], bids: dict[int, Bid], files: dict[int, BidFiles | None],
                       descriptions: dict[int, Description], searcher: Searcher, judge: Judge,
                       topics: dict[str, TopicDefinition]) -> tuple[dict[tuple[int, str], bool], set[tuple[int, str]], int]:
    """Today's title screen, with descriptions only for vague titles, then first-pages triage."""
    by_topic: dict[str, list[Label]] = {}
    for item in items:
        by_topic.setdefault(item.topic, []).append(item)
    reads: dict[tuple[int, str], bool] = {}
    kept: set[tuple[int, str]] = set()
    model_calls = 0

    for topic_key, labels in by_topic.items():
        topic = topics[topic_key]
        topic_bids = {item.b_id: bids[item.b_id] for item in labels}
        vague: set[int] = set()
        cards: dict[int, str] = {}
        for b_id, bid in topic_bids.items():
            if not is_vague_title(bid.title):
                continue
            row = descriptions.get(b_id)
            if row is not None and row.usable():
                cards[b_id] = row.text()
            else:
                vague.add(b_id)
        screened = [bid for b_id, bid in topic_bids.items() if b_id not in vague]
        screen_kept, unscreened = await searcher._screen_titles(topic, screened, cards)
        batch = searcher.settings.screen_batch
        model_calls += (len(screened) + batch - 1) // batch
        passed = vague | screen_kept | unscreened
        kept.update((b_id, topic_key) for b_id in passed)

        async def triage(b_id: int, topic=topic) -> bool:
            cached = files.get(b_id)
            if cached is None:
                return True  # unavailable evidence is not a routing miss
            try:
                verdict, _ = await judge.screen(topic, bids[b_id], cached)
                return verdict is None
            except Exception as exc:  # noqa: BLE001 - one failed bid must not stop the evaluation
                log.warning("first-pages check for %s failed: %s", b_id, exc)
                return True

        results = await asyncio.gather(*(triage(b_id) for b_id in sorted(passed)))
        model_calls += len(results)
        reads.update(((b_id, topic_key), read) for b_id, read in zip(sorted(passed), results, strict=True))
        reads.update(((b_id, topic_key), False) for b_id in topic_bids if b_id not in passed)
    return reads, kept, model_calls


def _route_reads(way: Way, items: list[Label], bids: dict[int, Bid], descriptions: dict[int, Description],
                 calls: dict[tuple[int, str], Call], today_reads: dict[tuple[int, str], bool],
                 kept: set[tuple[int, str]]) -> dict[tuple[int, str], bool]:
    safety_net = way == "today or A"
    reads: dict[tuple[int, str], bool] = {}
    for item in items:
        key = (item.b_id, item.topic)
        chosen = route(_candidate(item.topic, bids[item.b_id], kept=key in kept), descriptions.get(item.b_id),
                       calls.get(key), safety_net=safety_net)
        reads[key] = today_reads.get(key, True) if chosen in ("today", "net") else chosen in ("match", "unsure")
    return reads


def _retirement(scores: list[WayScore]) -> bool:
    subject_a = [score for score in scores if score.way == "A" and score.topics == "subject"]
    return bool(subject_a) and all(score.checked_missed == 0 for score in subject_a)


async def evaluate(labelled: LabelledSet, *, describer: Describer, matcher: Matcher, searcher: Searcher, judge: Judge,
                   index: BidIndex, cache: DocCache, repeats: int = 3, topic: str | None = None) -> MatchReport:
    if repeats < 1:
        raise ValueError("repeats must be at least 1")
    if topic is not None and topic not in labelled.topics:
        raise ValueError(f"unknown topic: {topic}")
    items = [item for item in labelled.items if topic is None or item.topic == topic]
    selected = labelled.model_copy(update={"items": items})
    descriptions = await describe_labelled(selected, describer, index, cache)
    labels = {item.b_id: item for item in items}
    bids = {b_id: _bid(label, index) for b_id, label in labels.items()}
    files = {b_id: _load_files(cache, b_id) for b_id in bids}
    topic_classes: list[Topics] = []
    if any(item.topic not in BUYER_TOPICS for item in items):
        topic_classes.append("subject")
    if any(item.topic in BUYER_TOPICS for item in items):
        topic_classes.append("buyer")

    scores: list[WayScore] = []
    for run in range(1, repeats + 1):
        with tempfile.TemporaryDirectory(prefix="gem-eval-match-") as scratch:
            judge.verdicts = VerdictCache(Path(scratch))
            today_reads, kept, today_calls = await _today_reads(items, bids, files, descriptions, searcher, judge,
                                                               labelled.topics)
            matcher.calls = 0
            calls: dict[tuple[int, str], Call] = {}
            for topic_key in dict.fromkeys(item.topic for item in items):
                match_items = [MatchItem(b_id=item.b_id, description=descriptions[item.b_id].text(),
                                         buyer=buyer_line(bids[item.b_id], descriptions[item.b_id].buyer_org))
                               for item in items if item.topic == topic_key and item.b_id in descriptions
                               and descriptions[item.b_id].usable()]
                if match_items:
                    decided = await matcher.match(labelled.topics[topic_key], match_items)
                    calls.update(((b_id, topic_key), call) for b_id, call in decided.items())
            match_calls = matcher.calls
            for way in ("today", "A", "today or A"):
                reads = today_reads if way == "today" else _route_reads(way, items, bids, descriptions, calls,
                                                                        today_reads, kept)
                way_calls = today_calls if way == "today" else match_calls + (today_calls if way == "today or A" else 0)
                for topics in topic_classes:
                    scores.append(score_way(way, reads, selected, topics=topics, run=run, model_calls=way_calls,
                                            descriptions=descriptions))
    return MatchReport(scores=scores, retire=_retirement(scores))


def report_text(report: MatchReport) -> str:
    subject_a = [score for score in report.scores if score.way == "A" and score.topics == "subject"]
    runs = len(subject_a)
    if report.retire:
        ending = (f"A alone missed no checked label in {runs} of {runs} runs: the safety net can be retired "
                  "(MATCH_SAFETY_NET=false). A person decides.")
    else:
        missed_runs = sum(score.checked_missed > 0 for score in subject_a)
        ending = f"Keep the safety net: A alone missed checked labels in {missed_runs} of {runs} runs."
    lines = ["Match evaluation (buyer topics are scored apart)",
             (f"{'way':<12}{'topics':<9}{'run':>4}{'checked missed':>15}{'all missed':>12}"
              f"{'negatives read':>17}{'model calls':>13}")]
    for score in report.scores:
        lines.append(f"{score.way:<12}{score.topics:<9}{score.run:>4}{score.checked_missed:>15}"
                     f"{score.all_missed:>12}{f'{score.negatives_read}/{score.negatives}':>17}"
                     f"{score.model_calls:>13}")
    missed = [score for score in report.scores if score.missed]
    if missed:
        lines.extend(["", "Missed tenders (topic:b_id: description or title):"])
        for score in missed:
            lines.append(f"  {score.way} / {score.topics} / run {score.run}")
            lines.extend(f"    {item}" for item in score.missed)
    lines.extend(["", ending])
    return "\n".join(lines)
