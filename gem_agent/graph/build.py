"""The run graph: plan → search → (widen / narrow, at most 2 retries) → process → search_rest →
process → report.

Searching has two stages: `search` reads page 1 of every search and keeps both its shortlist and the in-window bids the
title screen dropped or the cap cut. `process` describes that whole pool once, matches the descriptions to each topic,
routes every bid, keeps today's title and first-pages checks as the safety net, then fully reads matches, might-matches
and safety-net bids in that order. `search_rest` reads the remaining pages; the second `process` handles only bids not
already completed. The checkpointed `stage`, `done_ids`, `described` and `reads` keep a resumed run from repeating work
or exceeding the per-request limits.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from typing import Any

from langgraph.graph import END, START, StateGraph

from gem_agent.agents.chat import short_name
from gem_agent.agents.matcher import Call, MatchItem, buyer_line
from gem_agent.agents.schemas import TopicDefinition
from gem_agent.agents.searcher import Candidate, search_specs
from gem_agent.graph.deciding import describe_order, not_read_reason, route, title_similarity
from gem_agent.graph.rulings import settle, topic_for
from gem_agent.graph.services import Services
from gem_agent.graph.state import RunState
from gem_agent.graph.store import Outcome, ProcessEvent
from gem_agent.graph.views import bid_result, bid_state
from gem_agent.tools.gem.live import SearchProgress

log = logging.getLogger(__name__)
MAX_RETRIES = 2
WIDEN = ("The last search found no matching titles. Suggest broader extra_terms (synonyms, the wider field) and any "
         "categories that could hold such tenders.")
NARROW = "The last search matched too many titles. Suggest more specific extra_terms; avoid generic words."


def emit(s: Services, run_id: str, event: dict[str, Any]) -> None:
    s.store.append_event(run_id, event)
    s.bus.publish(run_id, event)


def progress(s: Services, run_id: str, text: str, kind: str | None = None, phase: str | None = None) -> None:
    """kind "count": the running count, which the chat shows as one line updated in place (views._progress).
    phase: the step the line belongs to ("plan", "search", "retry", "read", "search_rest", "report"), so a finished
    run's log rolls up into phases without reading the wording."""
    event: dict[str, Any] = {"at": s.now().isoformat(timespec="seconds"), "text": text}
    if kind:
        event["kind"] = kind
    if phase:
        event["phase"] = phase
    emit(s, run_id, {"type": "progress", "event": event})


def running_count(read: int, total: int, outcomes: Iterable[Outcome]) -> str:
    """What reading has done so far, counted from the stored outcomes (nothing is estimated)."""
    dropped = rejected = relevant = failed = 0
    for o in outcomes:
        if o.not_read:
            dropped += 1
        elif o.error:
            failed += 1
        elif o.verdict and o.verdict.relevant:
            relevant += 1
        elif o.verdict and o.verdict.stage == "triage":
            dropped += 1
        elif o.verdict:
            rejected += 1  # documents read in full, then quarantined
    parts = [f"{relevant} relevant", f"{dropped + rejected} not a match"]
    if failed:
        parts.append(f"{failed} could not be read")
    return f"Read {read} of {total} bids: {', '.join(parts)}."


def ways_line(n: int) -> str:
    return f"{n} way" if n == 1 else f"{n} ways"


def cut_line(n: int) -> str:
    return f"{n} more matching bid{'s' if n != 1 else ''} not read; narrow the request to see them."


def described_line(new: int, known: int) -> str:
    return f"Described {new} new bid{'s' if new != 1 else ''} ({known} already known)."


def matched_line(n: int, match: int, might: int) -> str:
    return f"Matched {n} bid{'s' if n != 1 else ''}: {match} match, {might} might."


def undescribed_line(n: int) -> str:
    return f"{n} bid{'s' if n != 1 else ''} not yet described; they will be checked on the next search."


def stopped_line(read: int, total: int, relevant: int, deeper_ran: bool) -> str:
    text = f"Stopped: read {read} of {total} bids, {relevant} relevant."
    return text if deeper_ran else f"{text} The deeper search did not run."


def error_line(read: int, total: int) -> str:
    return f"Stopped by an error after reading {read} of {total} bids. Try again; bids already read are kept."


Progress = dict[str, dict[str, SearchProgress]]  # topic.key() -> search key -> progress


def failed_line(failed: int, total: int) -> str:
    return f"{failed} of {total} GeM searches could not be read; results may be incomplete."


def skipped_line(terms: list[str]) -> str:
    """GeM answers a full-text search over 20,000 hits with no bids: the search was not read at all."""
    quoted = ", ".join(f"“{t}”" for t in terms)
    if len(terms) == 1:
        return f"Skipped {quoted}: it matches over 20,000 open bids, too broad to read."
    return f"Skipped {quoted}: they match over 20,000 open bids, too broad to read."


def failed_searches(after: Progress, before: Progress | None = None) -> tuple[int, int]:
    """(failed, read) over distinct searches, all topics. Stage 1 (no `before`): every search. Stage 2: the searches
    that still had pages, and those of them that failed now."""
    read: set[str] = set()
    failed: set[str] = set()
    for topic, searches in after.items():
        for key, p in searches.items():
            was = (before or {}).get(topic, {}).get(key)
            if before is not None and (was is None or not was.pending):
                continue
            read.add(key)
            if p.failed:
                failed.add(key)
    return len(failed), len(read)


def gem_failed(search_progress: Progress) -> bool:
    """Searches failed and none that worked returned any bids: an empty shortlist says nothing about the topic."""
    searches = [p for t in search_progress.values() for p in t.values()]
    return any(p.failed for p in searches) and not any(
        not p.failed and not p.skipped and p.num_found > 0 for p in searches)


def wants_retry(*, stage: str, retries: int, candidates: list[Candidate], over_cap: bool,
                search_progress: Progress, sizes: list[int]) -> bool:
    """After stage 1: narrow when the shortlist is over the cap. Widen when it is empty and no search has pages left:
    page 1 alone is the newest-published bids, so an empty page-1 shortlist goes on to stage 2 instead. Nor when GeM
    could not be read: a wider search would fail the same way.

    The cap's retry is only worth it when narrowing works: the Planner re-runs the topic's own terms every time, so a
    round that shrank the shortlist by less than a tenth will not get it under the cap. That retry is skipped."""
    if stage != "first" or retries >= MAX_RETRIES:
        return False
    if over_cap:
        return len(sizes) < 2 or sizes[-1] <= sizes[-2] * 0.9
    pending = any(p.pending for searches in search_progress.values() for p in searches.values())
    return not candidates and not pending and not gem_failed(search_progress)


def by_closing(candidates: Iterable[Candidate]) -> list[Candidate]:
    return sorted(candidates, key=lambda c: c.bid.closing.isoformat() if c.bid.closing else "9")


def unique_bids(candidates: Iterable[Candidate]) -> list[Candidate]:
    """One representative per bid, keeping the first topic's candidate (request order is relevance order)."""
    seen: set[int] = set()
    out: list[Candidate] = []
    for c in candidates:
        if c.bid.b_id not in seen:
            seen.add(c.bid.b_id)
            out.append(c)
    return out


def candidate_topic_key(c: Candidate, topics: Iterable[TopicDefinition]) -> str:
    """The stable topic key, resolving a pre-topic checkpoint by its display group when needed."""
    if c.topic_key:
        return c.topic_key
    return next((t.key() for t in topics if short_name(t) == c.group), c.group)


def build_graph(s: Services) -> StateGraph:
    async def plan(state: RunState) -> dict[str, Any]:
        note = WIDEN if state.adjust == "widen" else NARROW if state.adjust == "narrow" else ""
        plans = [await s.planner.plan(state.request, topic, note=note) for topic in state.request.topics]
        s.store.save_plans(state.run_id, plans)
        searches = sum(len(search_specs(p)) for p in plans)  # one plan runs several GeM searches, not one
        progress(s, state.run_id, f"Searching GeM {ways_line(searches)}.", phase="plan")
        return {"plans": plans}

    async def search(state: RunState) -> dict[str, Any]:
        """Stage 1: page 1 of every search, plus the in-window bids outside the shortlist."""
        merged: dict[tuple[str, int], Candidate] = {}
        rest: dict[tuple[str, int], Candidate] = {}
        pool, over_cap, cut = 0, False, 0
        integrated = s.describer is not None and s.matcher is not None
        search_progress: dict[str, Any] = {}
        first_counts: dict[str, int] = {}
        skipped: list[str] = []
        for topic, plan_ in zip(state.request.topics, state.plans, strict=True):
            result = await s.searcher.search(topic, plan_, stage="first")
            pool += result.pool
            over_cap |= result.over_cap
            cut += result.cut
            search_progress[topic.key()] = result.progress
            first_counts[topic.key()] = len(result.candidates)
            skipped += [key.partition("|")[2] for key, p in result.progress.items() if p.skipped]
            for c in result.candidates:
                merged.setdefault((c.identity, c.bid.b_id), c)
            for c in result.rest_pool:
                rest.setdefault((c.identity, c.bid.b_id), c)
        candidates = by_closing(merged.values())
        pool_rest = by_closing(c for key, c in rest.items() if key not in merged)
        sizes = [*state.sizes, len(candidates)]  # this round's shortlist size: over-cap retries are judged on it
        s.store.save_shortlist(state.run_id, candidates)
        shortlist_count = len({c.bid.b_id for c in candidates})
        if candidates:
            progress(s, state.run_id, f"Shortlist: {shortlist_count} bid{'s' if shortlist_count != 1 else ''} to read "
                                      f"from {pool:,} titles checked, closing soonest first.", phase="search")
        failed, read = failed_searches(search_progress)
        if failed:
            progress(s, state.run_id, failed_line(failed, read), phase="search")
        if skipped:
            progress(s, state.run_id, skipped_line(list(dict.fromkeys(skipped))), phase="search")
        if cut and not integrated and not wants_retry(stage=state.stage, retries=state.retries, candidates=candidates,
                                                      over_cap=over_cap, search_progress=search_progress, sizes=sizes):
            progress(s, state.run_id, cut_line(cut), phase="search")  # a narrowing retry comes next otherwise
        return {"candidates": candidates, "pool_rest": pool_rest, "pool": pool, "over_cap": over_cap,
                "search_progress": search_progress, "first_counts": first_counts, "cut": 0 if integrated else cut,
                "sizes": sizes}

    def after_search(state: RunState) -> str:
        if wants_retry(stage=state.stage, retries=state.retries, candidates=state.candidates, over_cap=state.over_cap,
                       search_progress=state.search_progress, sizes=state.sizes):
            return "retry"
        return "process"

    async def search_rest(state: RunState) -> dict[str, Any]:
        """Stage 2: remaining pages, with exclusions and cap slots scoped to each topic."""
        integrated = s.describer is not None and s.matcher is not None
        pool_rest = {(c.identity, c.bid.b_id): c for c in state.pool_rest}
        seen_pairs = {(c.identity, c.bid.b_id) for c in [*state.candidates, *state.pool_rest]}
        seen_by_topic: dict[str, set[int]] = {}
        for c in [*state.candidates, *state.pool_rest]:
            seen_by_topic.setdefault(c.identity, set()).add(c.bid.b_id)
        seen_ids = {c.bid.b_id for c in [*state.candidates, *state.pool_rest]}
        new: list[Candidate] = []
        pool, cut = state.pool, 0
        search_progress = dict(state.search_progress)
        searchable = []  # (topic, plan, the bids stage 1 kept) with pages left to read and places left in the cap
        for topic, plan_ in zip(state.request.topics, state.plans, strict=True):
            key = topic.key()
            if key not in search_progress:  # no stage 1 to continue (a state from before two stages)
                log.warning("run %s: no stage-1 progress for %s; not searching deeper", state.run_id, key)
                continue
            taken = state.first_counts.get(key)
            if taken is None:  # a checkpoint from before first_counts: its stage-1 bids, by identity/name
                taken = sum(1 for c in state.candidates
                            if c.identity == key or (c.topic_key is None and c.group == short_name(topic)))
            if taken < s.searcher.settings.cap:  # stage 1 filled the cap: no places left, so no pages to read
                searchable.append((topic, plan_, taken))
        if any(p.pending for t, _, _ in searchable for p in search_progress[t.key()].values()):
            # silent otherwise: page reads take minutes, and the chat shows a spinner meanwhile
            progress(s, state.run_id, "Looking through more GeM pages for older matches.", phase="search_rest")

        def more_found(bids: int, target: int) -> None:
            """One line, updated in place: the deeper pages cost a second each, so the count must move."""
            progress(s, state.run_id, f"More GeM pages checked: {bids} titles so far.", "count", phase="search_rest")

        def is_new(c: Candidate) -> bool:
            return (c.identity, c.bid.b_id) not in seen_pairs if integrated else c.bid.b_id not in seen_ids

        for topic, plan_, taken in searchable:
            key = topic.key()
            exclude = seen_by_topic.get(key, set()) if integrated else seen_ids
            result = await s.searcher.search(topic, plan_, stage="rest", progress=search_progress[key],
                                             exclude=set(exclude), cap=s.searcher.settings.cap - taken,
                                             on_page=more_found)
            pool += result.pool
            cut += result.cut
            search_progress[key] = result.progress
            for c in result.candidates:
                pair = (c.identity, c.bid.b_id)
                if is_new(c):
                    seen_pairs.add(pair)
                    seen_ids.add(c.bid.b_id)
                    seen_by_topic.setdefault(c.identity, set()).add(c.bid.b_id)
                    new.append(c)
            for c in result.rest_pool:
                pair = (c.identity, c.bid.b_id)
                if is_new(c):
                    seen_pairs.add(pair)
                    seen_ids.add(c.bid.b_id)
                    seen_by_topic.setdefault(c.identity, set()).add(c.bid.b_id)
                    pool_rest[pair] = c
        candidates = [*state.candidates, *by_closing(new)]
        if new:
            s.store.save_shortlist(state.run_id, candidates)
            count = len({c.bid.b_id for c in new})
            progress(s, state.run_id, f"Found {count} more bid{'s' if count != 1 else ''} to read.", phase="search_rest")
        if cut and not integrated:
            progress(s, state.run_id, cut_line(cut), phase="search_rest")
        failed, read = failed_searches(search_progress, before=state.search_progress)
        if failed:
            progress(s, state.run_id, failed_line(failed, read), phase="search_rest")
        return {"candidates": candidates, "pool_rest": by_closing(pool_rest.values()), "pool": pool,
                "search_progress": search_progress, "stage": "rest", "cut": state.cut if integrated else state.cut + cut}

    def after_process(state: RunState) -> str:
        return "search_rest" if state.stage == "first" else "report"

    async def retry(state: RunState) -> dict[str, Any]:
        adjust = "narrow" if state.candidates else "widen"
        progress(s, state.run_id, "Nothing matched by title; widening the search." if adjust == "widen"
                 else "Too many matches; narrowing the search.", phase="retry")
        return {"adjust": adjust, "retries": state.retries + 1}

    async def process_today(state: RunState) -> dict[str, Any]:
        slots = asyncio.Semaphore(s.workers)
        done = list(dict.fromkeys(state.done_ids))
        total = len({c.bid.b_id for c in state.candidates})
        outcomes = s.store.outcomes(state.run_id)

        async def one(c: Candidate) -> None:
            async with slots:
                topic = topic_for(state.request, c.group, topic_key=c.topic_key)
                outcome = Outcome(b_id=c.bid.b_id, group=c.group, topic_key=c.topic_key, bid=c.bid)
                try:
                    files = await s.collector.collect(c.bid, attachments=False)
                    verdict, triage = await s.judge.screen(topic, c.bid, files)
                    if verdict is None:
                        files = await s.collector.collect(c.bid)
                        verdict = await s.judge.judge(topic, c.bid, files, triage=triage)
                    outcome.files, outcome.verdict, outcome.buyer_org = files.files, verdict, files.buyer_org
                    outcome.ruling = await settle(s, state.user, topic, verdict)
                except Exception as exc:  # noqa: BLE001 - one bid failing never stops the run
                    log.warning("bid %s failed: %s", c.bid.bid_number, exc)
                    outcome.error = str(exc)[:300]
                outcomes[outcome.b_id] = outcome
                s.store.save_outcome(state.run_id, outcome)
                done.append(c.bid.b_id)
                emit(s, state.run_id, {"type": "result", "bid": bid_result(state.run_id, outcome)})
                count_line(len(done))

        def count_line(read: int) -> None:
            """After every bid, the last one included: the chat shows only the newest count, so lines don't pile up."""
            progress(s, state.run_id, running_count(read, total, (outcomes[b] for b in done if b in outcomes)), "count",
                     phase="read")

        todo = unique_bids(c for c in state.candidates if c.bid.b_id not in state.done_ids)
        if todo:
            count_line(len(done))  # at once, before the first bid finishes
        await asyncio.gather(*(one(c) for c in todo))
        return {"done_ids": done, **({"stage": "done"} if state.stage == "rest" else {})}

    async def process_matching(state: RunState) -> dict[str, Any]:
        """Describe the whole pool, match per topic, route, keep the safety net, then read in priority order."""
        slots = asyncio.Semaphore(s.workers)
        outcomes = s.store.outcomes(state.run_id)
        pairs: dict[tuple[str, int], Candidate] = {}
        for c in [*state.candidates, *state.pool_rest]:
            key = candidate_topic_key(c, state.request.topics)
            if c.topic_key != key:
                c = c.model_copy(update={"topic_key": key})
            pairs.setdefault((key, c.bid.b_id), c)
        all_bids = unique_bids(pairs.values())
        bids_by_id = {c.bid.b_id: c for c in all_bids}
        pairs_by_id: dict[int, list[Candidate]] = {}
        for c in pairs.values():
            pairs_by_id.setdefault(c.bid.b_id, []).append(c)
        topics = {topic.key(): topic for topic in state.request.topics}
        stored = s.store.process_progress(state.run_id)
        pending_reads = dict(stored.pending_reads)
        pair_routes = {(key, b_id): chosen for key, topic_routes in stored.routes.items()
                       for b_id, chosen in topic_routes.items() if (key, b_id) in pairs}
        done = list(dict.fromkeys([*state.done_ids, *stored.done_ids, *outcomes, *pending_reads]))
        done_set = set(done)
        reopened = {b_id for b_id in done_set if stored.routes and any(
            (c.identity, b_id) not in pair_routes for c in pairs_by_id[b_id])}
        if reopened:
            done = [b_id for b_id in done if b_id not in reopened]
            done_set = set(done)
        todo = [c for c in pairs.values() if c.bid.b_id not in done_set]
        total = len(all_bids)
        described = max(state.described, stored.described)
        reads: dict[str, int] = {}
        for source in (stored.reads, state.reads):
            for key, count in source.items():
                targets = [key] if key in topics else [t.key() for t in state.request.topics
                                                       if short_name(t) == key] or [key]
                for target in targets:
                    reads[target] = max(reads.get(target, 0), count)
        base_cut = max(state.cut, stored.cut)
        cut = 0
        files_by_id: dict[int, Any] = {}
        triage_by_id: dict[int, Any] = {}
        routes: dict[int, str] = {}
        calls = {key: {b_id: call for b_id, call in topic_calls.items() if (key, b_id) in pairs}
                 for key, topic_calls in stored.calls.items() if key in topics}
        if stored.legacy_calls and len(state.request.topics) == 1:
            key = state.request.topics[0].key()
            calls.setdefault(key, {}).update({b_id: call for b_id, call in stored.legacy_calls.items()
                                              if (key, b_id) in pairs})

        def record(kind: str, **kwargs: Any) -> None:
            s.store.append_process_event(state.run_id, ProcessEvent(kind=kind, **kwargs))

        def count_line() -> None:
            seen = [outcomes[b_id] for b_id in done if b_id in outcomes]
            progress(s, state.run_id, running_count(len(done), total, seen), "count", phase="read")

        def mark_done(c: Candidate) -> None:
            if c.bid.b_id not in done_set:
                done.append(c.bid.b_id)
                done_set.add(c.bid.b_id)
                record("done", b_id=c.bid.b_id)

        def save(c: Candidate, outcome: Outcome) -> None:
            if outcome.topic_key is None:
                outcome.topic_key = c.topic_key
            outcomes[c.bid.b_id] = outcome
            s.store.save_outcome(state.run_id, outcome)
            mark_done(c)
            emit(s, state.run_id, {"type": "result", "bid": bid_result(state.run_id, outcome)})
            count_line()

        def hide_previous_outcome(c: Candidate) -> None:
            """A later topic can turn an old displayed result into an unseen/cut bid; last-line-wins hides it."""
            if c.bid.b_id in outcomes:
                blank = Outcome(b_id=c.bid.b_id, group=c.group, topic_key=c.topic_key, bid=c.bid)
                outcomes[c.bid.b_id] = blank
                s.store.save_outcome(state.run_id, blank)

        def already_read(c: Candidate) -> bool:
            previous = outcomes.get(c.bid.b_id)
            return bool(previous and previous.verdict and previous.verdict.stage != "triage")

        async def finish_pending_reads() -> None:
            """Attach rulings to verdicts saved before an interruption, without judging or collecting again."""
            for b_id, outcome in list(pending_reads.items()):
                if b_id in outcomes:  # the outcome append succeeded before the sidecar could be cleared
                    del pending_reads[b_id]
                    record("pending_clear", b_id=b_id)
                    continue
                topic_key = outcome.topic_key or (outcome.verdict.topic_key if outcome.verdict else None)
                topic = topic_for(state.request, outcome.group, topic_key=topic_key)
                outcome.ruling = await settle(s, state.user, topic, outcome.verdict)
                try:
                    save(bids_by_id[b_id], outcome)
                except Exception as exc:  # noqa: BLE001 - a failed write must not poison every later resume
                    log.warning("bid %s: pending verdict could not be saved; leaving it pending: %s",
                                outcome.bid.bid_number, exc)
                    continue
                del pending_reads[b_id]
                record("pending_clear", b_id=b_id)

        if pending_reads:
            await finish_pending_reads()
        if not todo:
            return {"done_ids": done, "described": described, "reads": reads, "cut": base_cut,
                    **({"stage": "done"} if state.stage == "rest" else {})}
        if base_cut > state.cut:
            progress(s, state.run_id, cut_line(base_cut - state.cut), phase="read")
        count_line()

        known_all = s.describer.known(bids_by_id)
        known = {b_id: row for b_id, row in known_all.items()
                 if row.current(s.describer.model_name, s.describer.prompt_version)}
        known_count = len(known)
        missing = [c for c in todo if c.bid.b_id not in known]
        similarity: dict[int, float] = {}
        for topic in state.request.topics:
            topic_missing = [c for c in missing if c.identity == topic.key()]
            if topic_missing:
                scores = await title_similarity(s.similarity_embedder, topic,
                                                [c.bid.title for c in topic_missing])
                for c, score in zip(topic_missing, scores, strict=True):
                    similarity[c.bid.b_id] = max(similarity.get(c.bid.b_id, 0.0), score)
        queue = unique_bids(describe_order(missing, similarity))[:max(0, s.describe_cap - described)]

        ready: dict[str, list[MatchItem]] = {}

        def item(c: Candidate, description) -> MatchItem:
            return MatchItem(b_id=c.bid.b_id, description=description.text(),
                             buyer=buyer_line(c.bid, description.buyer_org))

        for c in todo:  # cached descriptions lead every topic's first batch
            description = known.get(c.bid.b_id)
            if c.bid.b_id not in calls.get(c.identity, {}) and description is not None and description.usable():
                ready.setdefault(c.identity, []).append(item(c, description))

        async def flush(topic_key: str) -> None:
            items = ready[topic_key]
            while items:
                batch = items[:40]
                del items[:40]
                decided = await s.matcher.match(topics[topic_key], batch)
                calls.setdefault(topic_key, {}).update(decided)
                for b_id, call in decided.items():
                    record("call", topic_key=topic_key, b_id=b_id, call=call)

        for topic_key in list(ready):
            await flush(topic_key)

        new = 0
        for c in queue:  # serial GeM reads: describing adds no traffic parallelism
            try:
                files = await s.collector.collect(c.bid, attachments=True)
            except Exception as exc:  # noqa: BLE001 - a document failure is this bid's error outcome
                log.warning("bid %s failed while describing: %s", c.bid.bid_number, exc)
                save(c, Outcome(b_id=c.bid.b_id, group=c.group, topic_key=c.topic_key, bid=c.bid,
                                error=str(exc)[:300]))
                continue
            files_by_id[c.bid.b_id] = files
            description = await s.describer.describe(c.bid, files)
            if description is None:
                continue
            known[c.bid.b_id] = description
            described += 1
            new += 1
            record("described")
            if description.usable():
                affected = {pair.identity for pair in pairs_by_id[c.bid.b_id]}
                for pair in pairs_by_id[c.bid.b_id]:
                    ready.setdefault(pair.identity, []).append(item(pair, description))
                for topic_key in affected:
                    if len(ready[topic_key]) >= 40:
                        await flush(topic_key)

        progress(s, state.run_id, described_line(new, max(0, known_count - state.described)), phase="read")
        undescribed = sum(1 for c in unique_bids(todo) if c.bid.b_id not in done_set and c.bid.b_id not in known)
        if undescribed:
            progress(s, state.run_id, undescribed_line(undescribed), phase="read")
        for topic_key in list(ready):
            await flush(topic_key)
        calls_by_bid: dict[int, list[Call]] = {}
        for topic_calls in calls.values():
            for b_id, call in topic_calls.items():
                calls_by_bid.setdefault(b_id, []).append(call)
        if calls_by_bid:
            matches = sum("match" in values for values in calls_by_bid.values())
            might = sum("match" not in values and "unsure" in values for values in calls_by_bid.values())
            progress(s, state.run_id, matched_line(len(calls_by_bid), matches, might), phase="read")

        priority = {"match": 0, "unsure": 1, "net": 2, "today": 3, "drop": 4, "unseen": 5}
        winners: dict[int, Candidate] = {}
        for c in todo:
            if c.bid.b_id in done_set:
                continue  # its documents failed while describing
            description = known.get(c.bid.b_id)
            chosen = route(c, description, calls.get(c.identity, {}).get(c.bid.b_id), safety_net=s.safety_net)
            pair_routes[(c.identity, c.bid.b_id)] = chosen
            record("route", topic_key=c.identity, b_id=c.bid.b_id, route=chosen)
            current = winners.get(c.bid.b_id)
            if current is None or priority[chosen] < priority[routes[c.bid.b_id]]:
                winners[c.bid.b_id] = c
                routes[c.bid.b_id] = chosen

        to_screen: list[Candidate] = []
        for representative in unique_bids(todo):
            c = winners.get(representative.bid.b_id)
            if c is None or c.bid.b_id in done_set:
                continue
            chosen = routes[c.bid.b_id]
            description = known.get(c.bid.b_id)
            if chosen in ("match", "unsure", "net", "today") and already_read(c):
                mark_done(c)  # the union still asks for a read, and this bid already has its one full read
                continue
            if chosen == "drop":
                files = files_by_id.get(c.bid.b_id)
                checks = ["the description check", "the title check"] if s.safety_net else ["the description check"]
                outcome = Outcome(b_id=c.bid.b_id, group=c.group, topic_key=c.topic_key, bid=c.bid,
                                  files=files.files if files else [],
                                  buyer_org=files.buyer_org if files else description.buyer_org,
                                  not_read=not_read_reason(description, checks))
                save(c, outcome)
            elif chosen == "unseen":
                hide_previous_outcome(c)
                mark_done(c)  # nothing to show, but this request must not try to describe it again in stage 2
            elif chosen in ("net", "today"):
                to_screen.append(c)
            else:
                triage_by_id[c.bid.b_id] = None

        async def screen(c: Candidate) -> None:
            async with slots:
                topic = topic_for(state.request, c.group, topic_key=c.topic_key)
                try:
                    files = await s.collector.collect(c.bid, attachments=False)
                    verdict, triage = await s.judge.screen(topic, c.bid, files)
                    files_by_id[c.bid.b_id] = files
                    if verdict is None:
                        triage_by_id[c.bid.b_id] = triage
                        return
                    outcome = Outcome(b_id=c.bid.b_id, group=c.group, topic_key=c.topic_key, bid=c.bid,
                                      files=files.files, buyer_org=files.buyer_org, verdict=verdict)
                    if routes[c.bid.b_id] == "net":
                        outcome.not_read = not_read_reason(
                            known.get(c.bid.b_id), ["the description check", "the first-pages check"])
                    outcome.ruling = await settle(s, state.user, topic, verdict)
                except Exception as exc:  # noqa: BLE001 - one bid failing never stops the run
                    log.warning("bid %s failed at the first-pages check: %s", c.bid.bid_number, exc)
                    outcome = Outcome(b_id=c.bid.b_id, group=c.group, topic_key=c.topic_key, bid=c.bid,
                                      error=str(exc)[:300])
                save(c, outcome)

        await asyncio.gather(*(screen(c) for c in to_screen))

        def unread(chosen: str) -> list[Candidate]:
            return by_closing(winners[b_id] for b_id in routes
                              if routes[b_id] == chosen and b_id not in done_set)

        ordered = by_closing(winners[b_id] for b_id in routes
                             if routes[b_id] in ("net", "today") and b_id not in done_set)
        selected: list[Candidate] = []
        planned_reads = dict(reads)
        for c in unread("match") + unread("unsure") + ordered:
            used = planned_reads.get(c.identity, 0)
            if used < s.searcher.settings.cap:
                selected.append(c)
                planned_reads[c.identity] = used + 1
            else:
                cut += 1
                record("cut")
                hide_previous_outcome(c)
                mark_done(c)  # already described and matched; later stages must not repeat that work

        async def read(c: Candidate) -> None:
            async with slots:
                topic = topic_for(state.request, c.group, topic_key=c.topic_key)
                try:
                    files = await s.collector.collect(c.bid)
                    files_by_id[c.bid.b_id] = files
                    if s.describer.stale(c.bid.b_id, files):
                        refreshed = await s.describer.describe(c.bid, files)
                        if refreshed is not None:
                            known[c.bid.b_id] = refreshed
                    verdict = await s.judge.judge(topic, c.bid, files, triage=triage_by_id.get(c.bid.b_id))
                except Exception as exc:  # noqa: BLE001 - collection or judging failed, before a verdict existed
                    log.warning("bid %s failed while being read: %s", c.bid.bid_number, exc)
                    save(c, Outcome(b_id=c.bid.b_id, group=c.group, topic_key=c.topic_key, bid=c.bid,
                                    error=str(exc)[:300]))
                    return

                chosen = routes[c.bid.b_id]
                outcome = Outcome(b_id=c.bid.b_id, group=c.group, topic_key=c.topic_key, bid=c.bid,
                                  files=files.files, buyer_org=files.buyer_org, verdict=verdict,
                                  read_because=None if chosen == "today" else chosen)
                reads[c.identity] = reads.get(c.identity, 0) + 1
                record("read", topic_key=c.identity)
                pending_reads[c.bid.b_id] = outcome
                record("pending", outcome=outcome)
                mark_done(c)  # atomically persist the finished read, its verdict, and its consumed cap slot
                try:
                    outcome.ruling = await settle(s, state.user, topic, verdict)
                except Exception as exc:  # noqa: BLE001 - a settlement failure leaves the completed verdict borderline
                    log.warning("bid %s could not be settled: %s", c.bid.bid_number, exc)
                save(c, outcome)
                del pending_reads[c.bid.b_id]
                record("pending_clear", b_id=c.bid.b_id)

        await asyncio.gather(*(read(c) for c in selected))
        if cut:
            progress(s, state.run_id, cut_line(cut), phase="read")
        return {"done_ids": done, "described": described, "reads": reads, "cut": base_cut + cut,
                **({"stage": "done"} if state.stage == "rest" else {})}

    async def process(state: RunState) -> dict[str, Any]:
        if s.describer is None or s.matcher is None:
            return await process_today(state)
        return await process_matching(state)

    async def report(state: RunState) -> dict[str, Any]:
        outcomes = list(s.store.outcomes(state.run_id).values())
        states = [bid_state(o) for o in outcomes if o.verdict or o.not_read]
        relevant, quarantined, borderline = (states.count(k) for k in ("relevant", "quarantined", "borderline"))
        failed = sum(1 for o in outcomes if o.error)
        searches = [p for t in state.search_progress.values() for p in t.values()]
        integrated = s.describer is not None and s.matcher is not None
        has_pool = bool(state.candidates or (integrated and state.pool_rest))
        if not has_pool and searches and all(p.failed for p in searches):
            total = len({k for t in state.search_progress.values() for k in t})
            text = f"GeM could not be searched: all {total} searches failed. Try again in a few minutes."
        elif not has_pool:
            text = (f"No matching tenders found {state.request.window.describe()} "
                    f"(looked at {state.pool:,} titles, searched {ways_line(state.retries + 1)}).")
        else:
            text = f"Done: {relevant} relevant, {quarantined} quarantined" + (f", {failed} could not be read" if failed else "") + "."
            text += (" 1 bid needs your call: see Borderline." if borderline == 1
                     else f" {borderline} bids need your call: see Borderline." if borderline else "")
            if state.cut:
                text += " " + cut_line(state.cut)
        progress(s, state.run_id, text, phase="report")
        return {}

    graph = StateGraph(RunState)
    for name, fn in (("plan", plan), ("search", search), ("retry", retry), ("process", process),
                     ("search_rest", search_rest), ("report", report)):
        graph.add_node(name, fn)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "search")
    graph.add_conditional_edges("search", after_search, {"retry": "retry", "process": "process"})
    graph.add_edge("retry", "plan")
    graph.add_conditional_edges("process", after_process, {"search_rest": "search_rest", "report": "report"})
    graph.add_edge("search_rest", "process")
    graph.add_edge("report", END)
    return graph
