import asyncio
import json

import pytest

from gem_agent.agents.rulings import RulingMatch
from gem_agent.tools.gem.live import SearchProgress
from gem_agent.graph.events import EventBus
from gem_agent.graph.manager import RunManager
from gem_agent.graph.services import Services
from gem_agent.graph.store import RunStore
from gem_agent.memory.user_memory import MemoryStore
from tests.agents.helpers import LOS
from tests.graph.fakes import (
    NOW,
    BlockingCollector,
    FakeChat,
    FakeCollector,
    FakeJudge,
    FakePlanner,
    FakeRulings,
    FakeSearcher,
    bid,
)

SIGHT = LOS.model_copy(update={"meaning": "Line of Sight: radio survey for telecom towers"})


def services(tmp_path, *, chat=None, searcher=None, collector=None, judge=None, planner=None, rulings=None,
             checkpoint=":memory:", cards=None):
    return Services(chat=chat or FakeChat(), planner=planner or FakePlanner(),
                    searcher=searcher or FakeSearcher([[bid(1), bid(2, "Lockers", 18)]]),
                    collector=collector or FakeCollector(), judge=judge or FakeJudge(),
                    store=RunStore(tmp_path / "runs"), bus=EventBus(), memory=MemoryStore(tmp_path), now=lambda: NOW,
                    checkpoint_path=checkpoint, rulings=rulings, cards=cards)


async def ask(manager, text, run_id=None, user="asha"):
    return [e async for e in manager.ask(user, text, run_id=run_id)]


async def test_a_search_streams_understanding_progress_and_each_result(tmp_path):
    s = services(tmp_path, judge=FakeJudge(no={2}))
    events = await ask(RunManager(s), "LOS tenders published in the last 2 weeks")
    types = [e["type"] for e in events]
    assert types[0] == "started" and types[1] == "understood" and types[-1] == "done"
    assert types.count("started") == 1 and types.count("understood") == 1  # 16 Nov live run printed them twice
    assert types.count("result") == 2
    progress = [e["event"]["text"] for e in events if e["type"] == "progress"]
    understood = next(e["understood"] for e in events if e["type"] == "understood")
    assert understood["summary"].startswith("Looking for ") and understood["summary"].endswith("published 2–16 Nov.")
    assert understood["not_this"] == LOS.not_this
    assert not any(p.startswith("Looking for") for p in progress)  # stated once, in the card
    assert any(p.startswith("Shortlist: 2 bids") for p in progress)
    assert progress[-1] == "Done: 1 relevant, 1 quarantined."
    run_id = events[0]["run"]["run_id"]
    assert s.store.load(run_id).status == "complete"


async def test_a_borderline_bid_is_counted_apart_in_the_report(tmp_path):
    s = services(tmp_path, searcher=FakeSearcher([[bid(1), bid(2, "Lockers", 18), bid(3, "Maybe LOS", 19)]]),
                 judge=FakeJudge(no={2}, borderline={3}))
    events = await ask(RunManager(s), "LOS tenders")
    progress = [e["event"]["text"] for e in events if e["type"] == "progress"]
    assert progress[-1] == "Done: 1 relevant, 1 quarantined. 1 bid needs your call: see Borderline."


async def test_results_arrive_closing_soonest_first(tmp_path):
    s = services(tmp_path, searcher=FakeSearcher([[bid(1, closing_day=25), bid(2, closing_day=18)]]))
    events = await ask(RunManager(s), "LOS tenders")
    assert [e["bid"]["b_id"] for e in events if e["type"] == "result"] == [2, 1]


async def test_clear_no_at_triage_skips_the_attachments(tmp_path):
    collector, judge = FakeCollector(), FakeJudge(no={2})
    s = services(tmp_path, collector=collector, judge=judge)
    events = await ask(RunManager(s), "LOS tenders")
    assert (2, True) not in collector.calls and (2, False) in collector.calls
    assert judge.judged == [1]
    quarantined = next(e["bid"] for e in events if e["type"] == "result" and e["bid"]["b_id"] == 2)
    assert not quarantined["relevant"] and quarantined["reason"] == "Lockers."


async def test_a_failing_bid_is_reported_and_the_run_goes_on(tmp_path):
    s = services(tmp_path, collector=FakeCollector(fail={2}))
    events = await ask(RunManager(s), "LOS tenders")
    assert events[-1] == {"type": "done", "status": "complete"}
    assert "1 could not be read" in [e["event"]["text"] for e in events if e["type"] == "progress"][-1]


async def test_empty_shortlist_widens_at_most_twice(tmp_path):
    planner, searcher = FakePlanner(), FakeSearcher([[]], more_pages=False)
    s = services(tmp_path, planner=planner, searcher=searcher)
    events = await ask(RunManager(s), "LOS tenders")
    assert searcher.calls == 3 and len(planner.notes) == 3 and "broader" in planner.notes[1]
    assert any("No matching tenders" in e["event"]["text"] for e in events if e["type"] == "progress")
    assert events[-1]["type"] == "done"


async def test_two_meanings_are_searched_and_grouped(tmp_path):
    s = services(tmp_path, chat=FakeChat(topics=[LOS, SIGHT]),
                 searcher=FakeSearcher([[bid(1)], [bid(5, "Line of sight survey")]]))
    events = await ask(RunManager(s), "LOS tenders")
    groups = {e["bid"]["b_id"]: e["bid"]["group"] for e in events if e["type"] == "result"}
    assert groups == {1: "Loan Origination System", 5: "Line of Sight"}


async def test_show_rejected_answers_on_the_current_run(tmp_path):
    s = services(tmp_path)
    manager = RunManager(s)
    first = await ask(manager, "LOS tenders")
    run_id = first[0]["run"]["run_id"]
    s.chat = FakeChat(intent="show_rejected", topics=[])
    events = await ask(manager, "show me the rejected ones", run_id=run_id)
    assert {"type": "show", "tab": "quarantine"} in events
    assert not any(e["type"] == "started" for e in events)
    assert len(s.store.list_runs("asha")) == 1


async def test_memory_question_opens_the_memory_tab(tmp_path):
    s = services(tmp_path, chat=FakeChat(intent="memory_list", topics=[]))
    events = await ask(RunManager(s), "what do you remember about me?")
    assert {"type": "show", "tab": "memory"} in events and any(e["type"] == "message" for e in events)


async def test_refinement_starts_a_follow_on_run_with_history(tmp_path):
    chat = FakeChat()
    s = services(tmp_path, chat=chat)
    manager = RunManager(s)
    first = await ask(manager, "LOS tenders")
    chat.intent = "refine"
    second = await ask(manager, "only cooperative banks", run_id=first[0]["run"]["run_id"])
    assert chat.calls[1][2] == ["LOS tenders"]
    assert second[0]["run"]["parent_run_id"] == first[0]["run"]["run_id"]


async def test_restore_moves_a_bid_to_relevant(tmp_path):
    s = services(tmp_path, judge=FakeJudge(no={2}))
    manager = RunManager(s)
    run_id = (await ask(manager, "LOS tenders"))[0]["run"]["run_id"]
    await manager.restore("asha", run_id, 2)
    view = manager.view("asha", run_id)
    restored = next(b for b in view["results"] if b["b_id"] == 2)
    assert restored["relevant"] and restored["restored"]


async def test_runs_are_private_to_their_user(tmp_path):
    s = services(tmp_path)
    manager = RunManager(s)
    run_id = (await ask(manager, "LOS tenders", user="asha"))[0]["run"]["run_id"]
    assert manager.list_runs("ravi") == []
    with pytest.raises(PermissionError):
        manager.view("ravi", run_id)


async def test_run_view_has_what_the_web_chat_needs(tmp_path):
    s = services(tmp_path, judge=FakeJudge(no={2}))
    manager = RunManager(s)
    run_id = (await ask(manager, "LOS tenders"))[0]["run"]["run_id"]
    view = manager.view("asha", run_id)
    assert view["understood"]["name"] == "Loan Origination System" and view["understood"]["window"] == "published 2–16 Nov"
    assert view["status"] == "complete" and view["zip_url"] == f"/api/runs/{run_id}/zip"
    result = next(b for b in view["results"] if b["b_id"] == 1)
    assert result["buyer"] == "Ministry of Finance / Department of Financial Services"
    assert result["files"][1] == {"name": "RFP.pdf", "title": None, "also": [], "copies": 1, "filename": "RFP.pdf",
                                  "pages": 40, "status": "ok", "download_name": "GEM-2026-B-1 - RFP.pdf",
                                  "url": f"/api/runs/{run_id}/files/1/rfp1"}
    assert result["files"][0]["name"] == "Bid document"
    assert result["evidence"][0]["verified"] and result["key_facts"]["emd"] == "Rs 5 lakh"
    assert result["queries"]["when"] == "2026-11-18T17:00:00" and result["queries"]["verified"]
    assert result["gem_url"] == "https://bidplus.gem.gov.in/showbidDocument/1"
    [item] = manager.list_runs("asha")
    assert item["found"] == 1 and item["title"] == "LOS tenders"


class CrashOnce(FakeSearcher):
    async def search(self, topic, plan, **kw):
        if self.calls == 0:
            self.calls += 1
            raise RuntimeError("process killed")
        return await super().search(topic, plan, **kw)


async def test_a_crashed_run_resumes_from_its_checkpoint(tmp_path):
    planner = FakePlanner()
    s = services(tmp_path, planner=planner, searcher=CrashOnce([[bid(1)]]), checkpoint=tmp_path / "checkpoints.sqlite")
    manager = RunManager(s)
    events = await ask(manager, "LOS tenders")
    run_id = events[0]["run"]["run_id"]
    assert events[-1] == {"type": "done", "status": "failed"} and s.store.load(run_id).status == "failed"
    await manager.resume(run_id)
    assert s.store.load(run_id).status == "complete"
    assert len(planner.notes) == 1  # planning was not redone
    assert manager.view("asha", run_id)["results"][0]["b_id"] == 1


async def test_stop_cancels_the_run_and_keeps_what_was_judged(tmp_path):
    import asyncio

    collector = BlockingCollector()
    s = services(tmp_path, collector=collector)
    manager = RunManager(s)
    events = []

    async def follow():
        async for e in manager.ask("asha", "LOS tenders"):
            events.append(e)

    task = asyncio.create_task(follow())
    while not any(e["type"] == "started" for e in events):
        await asyncio.sleep(0.01)
    run_id = events[0]["run"]["run_id"]
    await manager.stop("asha", run_id)
    await asyncio.wait_for(task, timeout=5)
    assert events[-1] == {"type": "done", "status": "stopped"}
    assert s.store.load(run_id).status == "stopped"
    assert manager.view("asha", run_id)["status"] == "stopped"


async def test_stopping_a_finished_run_is_refused(tmp_path):
    from gem_agent.graph.manager import RunNotRunning

    s = services(tmp_path)
    manager = RunManager(s)
    run_id = (await ask(manager, "LOS tenders"))[0]["run"]["run_id"]
    with pytest.raises(RunNotRunning):
        await manager.stop("asha", run_id)


async def test_stop_is_refused_for_another_users_run(tmp_path):
    import asyncio

    s = services(tmp_path, collector=BlockingCollector())
    manager = RunManager(s)
    events = []

    async def follow():
        async for e in manager.ask("asha", "LOS tenders"):
            events.append(e)

    task = asyncio.create_task(follow())
    while not any(e["type"] == "started" for e in events):
        await asyncio.sleep(0.01)
    run_id = events[0]["run"]["run_id"]
    with pytest.raises(PermissionError):
        await manager.stop("ravi", run_id)
    await manager.stop("asha", run_id)
    await asyncio.wait_for(task, timeout=5)


async def test_reading_reports_a_running_count_from_the_start_to_the_last_bid(tmp_path):
    bids = [bid(i, closing_day=18 + i % 5) for i in range(1, 21)]
    s = services(tmp_path, searcher=FakeSearcher([bids]), judge=FakeJudge(no={3, 4, 5}), collector=FakeCollector(fail={7}))
    events = await ask(RunManager(s), "LOS tenders")
    progress = [e["event"] for e in events if e["type"] == "progress"]
    texts = [p["text"] for p in progress]
    counts = [p["text"] for p in progress if p.get("kind") == "count"]
    assert texts[0].startswith("Planned 1 search")
    assert texts[1].startswith("Shortlist: 20 bids")
    assert texts[2] == counts[0] == "Read 0 of 20 bids: 0 files downloaded, triage dropped 0, 0 relevant so far…"  # at once
    assert len(counts) == 21  # then after every bid; the chat shows only the newest
    # 16 judged bids x 2 files + 3 triage rejections x 1 (bid document only); bid 7 failed
    assert counts[-1] == "Read 20 of 20 bids: 35 files downloaded, triage dropped 3, 16 relevant so far, 1 could not be read…"
    assert texts[-2:] == [counts[-1], "Done: 16 relevant, 3 quarantined, 1 could not be read."]


def test_every_bid_read_is_in_the_running_count():
    from gem_agent.graph.build import running_count
    from gem_agent.graph.store import Outcome
    judge, topic = FakeJudge(), LOS

    def outcome(b_id, relevant, stage, error=""):
        v = judge._verdict(topic, bid(b_id), relevant, stage)
        return Outcome(b_id=b_id, group="LOS", bid=bid(b_id), verdict=None if error else v, error=error)
    line = running_count(4, 9, [outcome(1, True, "judge"), outcome(2, False, "triage"), outcome(3, False, "judge"),
                                outcome(4, False, "judge", error="503")])
    assert line == ("Read 4 of 9 bids: 0 files downloaded, triage dropped 1, 1 quarantined after reading, "
                    "1 relevant so far, 1 could not be read…")


def test_runs_from_before_the_card_do_not_repeat_the_meaning_line():
    from gem_agent.graph.views import _progress
    line = {"at": "2026-11-16T13:40:00", "text": "Looking for LOS tenders published 2–16 Nov (…)."}
    shortlist = {"at": "2026-11-16T13:40:09", "text": "Shortlist: 9 bids"}
    events = [{"type": "started"}, {"type": "understood"}, {"type": "progress", "event": line},
              {"type": "progress", "event": shortlist}]
    assert _progress(events) == [shortlist]


async def test_the_shortlist_line_right_after_the_card_is_kept(tmp_path):
    from gem_agent.graph.views import run_view
    s = services(tmp_path)
    events = await ask(RunManager(s), "LOS tenders")
    run_id = events[0]["run"]["run_id"]
    view = run_view(s.store.load(run_id), s.store.outcomes(run_id), s.store.events(run_id))
    texts = [p["text"] for p in view["progress"]]
    assert texts[0].startswith("Planned 1 search")
    assert texts[1].startswith("Shortlist: 2 bids")  # kept, not swallowed by the meaning card


def test_count_lines_sent_without_a_kind_are_marked_as_counts():
    from gem_agent.graph.views import _progress
    old = {"at": "2026-11-17T15:22:27", "text": "Read 4 of 33 bids: 31 files downloaded, triage dropped 1…"}
    done = {"at": "2026-11-17T15:24:32", "text": "Done: 9 relevant, 24 quarantined."}
    assert _progress([{"type": "progress", "event": old}, {"type": "progress", "event": done}]) == [
        {**old, "kind": "count"}, done]


Q = "Does bid 2 count for you?"
TWO = lambda: [[bid(1), bid(2, "Lockers", 18)]]  # noqa: E731


def rule(s, topic="Loan Origination System"):
    return s.memory.add_ruling("asha", "Core banking add-ons count.", topic=topic, run_id="r0", today=NOW.date(),
                               evidence="e")


async def result_of(events, b_id):
    return next(e["bid"] for e in events if e["type"] == "result" and e["bid"]["b_id"] == b_id)


async def test_a_borderline_bid_waits_for_the_user(tmp_path):
    s = services(tmp_path, judge=FakeJudge(borderline={2}))
    b2 = await result_of(await ask(RunManager(s), "LOS tenders"), 2)
    assert b2["state"] == "borderline" and b2["question"]


async def test_an_earlier_ruling_on_the_same_topic_decides_it(tmp_path):
    s = services(tmp_path, judge=FakeJudge(borderline={2}), rulings=FakeRulings())
    fact = rule(s)
    s.rulings.answers[Q] = RulingMatch(fact_id=fact.id, decision="counts")
    b2 = await result_of(await ask(RunManager(s), "LOS tenders"), 2)
    assert b2["state"] == "relevant" and b2["ruling"]["by"] == "earlier"
    assert b2["ruling"]["fact"] == "Core banking add-ons count."


async def test_a_ruling_on_another_topic_is_not_used(tmp_path):
    s = services(tmp_path, judge=FakeJudge(borderline={2}), rulings=FakeRulings())
    fact = rule(s, topic="AI platform")
    s.rulings.answers[Q] = RulingMatch(fact_id=fact.id, decision="counts")
    b2 = await result_of(await ask(RunManager(s), "LOS tenders"), 2)
    assert s.rulings.resolved == [] and b2["state"] == "borderline"


async def test_a_failing_match_leaves_the_bid_borderline(tmp_path):
    s = services(tmp_path, judge=FakeJudge(borderline={2}), rulings=FakeRulings(fail=True))
    rule(s)
    b2 = await result_of(await ask(RunManager(s), "LOS tenders"), 2)
    assert b2["state"] == "borderline" and not b2.get("error")


async def test_a_ruling_made_mid_run_reaches_bids_judged_after_it(tmp_path):
    collector = BlockingCollector()
    s = services(tmp_path, collector=collector, judge=FakeJudge(borderline={2}), rulings=FakeRulings())
    s.workers = 1
    manager = RunManager(s)
    task = asyncio.create_task(ask(manager, "LOS tenders"))
    await asyncio.sleep(0.2)
    fact = rule(s)  # made while bid 2 (first to close) is still blocked
    s.rulings.answers[Q] = RulingMatch(fact_id=fact.id, decision="counts")
    collector.release.set()
    b2 = await result_of(await task, 2)
    assert b2["state"] == "relevant" and b2["ruling"]["by"] == "earlier"


# --- two stages: judge page 1, then search deeper (spec 6.2) ---------------------------------------------------------


async def test_a_run_judges_page_one_bids_then_searches_deeper_and_judges_the_new_ones(tmp_path):
    searcher, judge = FakeSearcher([[bid(1), bid(2, closing_day=21)]], rest=[[bid(2, closing_day=21), bid(3, closing_day=19)]]), FakeJudge()
    s = services(tmp_path, searcher=searcher, judge=judge)
    events = await ask(RunManager(s), "LOS tenders")
    assert searcher.stages == ["first", "rest"] and judge.judged == [1, 2, 3]
    assert searcher.excluded == [{1, 2}]  # every bid already in the run
    assert [e["bid"]["b_id"] for e in events if e["type"] == "result"] == [1, 2, 3]
    progress = [e["event"]["text"] for e in events if e["type"] == "progress"]
    assert "Searching deeper: 1 more bids to read…" in progress
    assert progress[-1] == "Done: 3 relevant, 0 quarantined."
    run_id = events[0]["run"]["run_id"]
    shortlist = json.loads((s.store.root / run_id / "shortlist.json").read_text())
    assert [r["b_id"] for r in shortlist] == [1, 2, 3]


async def test_a_bid_the_second_stage_returns_again_is_judged_once(tmp_path):
    searcher = FakeSearcher([[bid(1), bid(2)]], rest=[[bid(2), bid(3)]], ignore_exclude=True)
    judge = FakeJudge()
    s = services(tmp_path, searcher=searcher, judge=judge)
    events = await ask(RunManager(s), "LOS tenders")
    assert sorted(judge.judged) == [1, 2, 3] and len(judge.judged) == 3
    assert sorted(e["bid"]["b_id"] for e in events if e["type"] == "result") == [1, 2, 3]
    assert "Searching deeper: 1 more bids to read…" in [e["event"]["text"] for e in events if e["type"] == "progress"]


async def test_no_deeper_line_when_the_second_stage_finds_nothing_new(tmp_path):
    s = services(tmp_path, searcher=FakeSearcher([[bid(1)]], rest=[[bid(1)]]))
    events = await ask(RunManager(s), "LOS tenders")
    assert not any(e["event"]["text"].startswith("Searching deeper") for e in events if e["type"] == "progress")
    assert events[-1] == {"type": "done", "status": "complete"}


async def test_the_second_stage_does_not_retry(tmp_path):
    planner, searcher = FakePlanner(), FakeSearcher([[bid(1)]], rest=[[]])
    s = services(tmp_path, planner=planner, searcher=searcher)
    await ask(RunManager(s), "LOS tenders")
    assert searcher.stages == ["first", "rest"] and len(planner.notes) == 1


async def test_an_empty_first_page_shortlist_searches_deeper_before_widening(tmp_path):
    # "closing in the next 5 days": page 1 is the newest-published bids, which rarely close soon
    planner, searcher = FakePlanner(), FakeSearcher([[]], rest=[[bid(3)]])
    s = services(tmp_path, planner=planner, searcher=searcher)
    events = await ask(RunManager(s), "LOS tenders closing in the next 5 days")
    assert searcher.stages == ["first", "rest"] and len(planner.notes) == 1
    assert [e["bid"]["b_id"] for e in events if e["type"] == "result"] == [3]


FAILED = SearchProgress(num_found=0, pages_read=0, page_count=0, failed=True)


async def test_searches_gem_could_not_read_are_reported_for_each_stage(tmp_path):
    midway = SearchProgress(num_found=40, pages_read=2, page_count=4, failed=True)
    searcher = FakeSearcher([[bid(1)]], rest=[[bid(2)]],
                            searches={"first": {"exact|BANKING SECTOR": FAILED}, "rest": {"fake": midway}})
    s = services(tmp_path, searcher=searcher)
    events = await ask(RunManager(s), "LOS tenders")
    progress = [e["event"]["text"] for e in events if e["type"] == "progress"]
    assert "1 of 2 GeM searches could not be read; results may be incomplete." in progress  # stage 1
    assert "1 of 1 GeM searches could not be read; results may be incomplete." in progress  # stage 2: what it read
    assert events[-1] == {"type": "done", "status": "complete"}


async def test_when_gem_cannot_be_searched_the_run_says_so_and_does_not_widen(tmp_path):
    planner = FakePlanner()
    searcher = FakeSearcher([[]], searches={"first": {"fake": FAILED, "exact|BANKING SECTOR": FAILED}})
    s = services(tmp_path, planner=planner, searcher=searcher)
    events = await ask(RunManager(s), "LOS tenders")
    assert len(planner.notes) == 1  # widening would only search a GeM that is down again
    progress = [e["event"]["text"] for e in events if e["type"] == "progress"]
    assert "2 of 2 GeM searches could not be read; results may be incomplete." in progress
    assert progress[-1] == "GeM could not be searched: all 2 searches failed. Try again in a few minutes."
    assert events[-1] == {"type": "done", "status": "complete"}


async def test_a_partly_failed_search_with_nothing_readable_does_not_widen(tmp_path):
    planner = FakePlanner()
    searcher = FakeSearcher([[]], more_pages=False, searches={"first": {"exact|BANKING SECTOR": FAILED}})
    s = services(tmp_path, planner=planner, searcher=searcher)
    events = await ask(RunManager(s), "LOS tenders")
    assert len(planner.notes) == 1  # the one search that worked matched no bids: the empty result is GeM's failure
    assert "No matching tenders found" in [e["event"]["text"] for e in events if e["type"] == "progress"][-1]


async def test_bids_found_only_by_the_second_stage_are_reported(tmp_path):
    s = services(tmp_path, searcher=FakeSearcher([[]], rest=[[bid(3)]]))
    events = await ask(RunManager(s), "LOS tenders")
    assert [e["bid"]["b_id"] for e in events if e["type"] == "result"] == [3]
    assert [e["event"]["text"] for e in events if e["type"] == "progress"][-1] == "Done: 1 relevant, 0 quarantined."


async def test_each_meaning_searches_deeper_with_its_own_progress(tmp_path):
    searcher = FakeSearcher([[bid(1)], [bid(5, "Line of sight survey")]], rest=[[bid(6)], [bid(7)]])
    s = services(tmp_path, chat=FakeChat(topics=[LOS, SIGHT]), searcher=searcher)
    events = await ask(RunManager(s), "LOS tenders")
    assert searcher.stages == ["first", "first", "rest", "rest"]
    assert searcher.excluded == [{1, 5}, {1, 5, 6}]
    groups = {e["bid"]["b_id"]: e["bid"]["group"] for e in events if e["type"] == "result"}
    assert groups == {1: "Loan Origination System", 5: "Line of Sight", 6: "Loan Origination System", 7: "Line of Sight"}


async def test_the_cap_is_per_topic_across_both_stages_and_what_it_cuts_is_reported(tmp_path):
    searcher = FakeSearcher([[bid(1), bid(2)]], rest=[[bid(3), bid(4), bid(5)]], cap=3)
    judge = FakeJudge()
    s = services(tmp_path, searcher=searcher, judge=judge)
    events = await ask(RunManager(s), "LOS tenders")
    assert searcher.caps == [None, 1]  # stage 2 gets what stage 1 left of the topic's cap
    assert sorted(judge.judged) == [1, 2, 3]
    progress = [e["event"]["text"] for e in events if e["type"] == "progress"]
    assert "2 more matching bids were not read; narrow the request to see them." in progress
    assert progress[-1] == ("Done: 3 relevant, 0 quarantined. 2 more matching bids were not read; "
                            "narrow the request to see them.")


async def test_a_first_stage_cut_is_reported_once_the_narrowing_retries_are_used(tmp_path):
    planner = FakePlanner()
    searcher = FakeSearcher([[bid(1), bid(2), bid(3)]], rest=[[bid(4)]], cap=2)
    s = services(tmp_path, planner=planner, searcher=searcher)
    events = await ask(RunManager(s), "LOS tenders")
    assert len(planner.notes) == 3 and "more specific" in planner.notes[1]  # narrowed twice, then read
    assert searcher.caps[-1] == 0  # nothing of the cap is left for stage 2
    progress = [e["event"]["text"] for e in events if e["type"] == "progress"]
    assert progress.count("1 more matching bid was not read; narrow the request to see them.") == 2  # each stage's cut
    assert progress[-1].endswith(" 2 more matching bids were not read; narrow the request to see them.")


class CrashInRest(FakeSearcher):
    """The process dies once, as stage 2 starts: the last checkpoint is the one after the first `process`."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.crashed = False

    async def search(self, topic, plan, *, stage="first", **kw):
        if stage == "rest" and not self.crashed:
            self.crashed = True
            raise RuntimeError("process killed")
        return await super().search(topic, plan, stage=stage, **kw)


async def test_a_resumed_run_continues_with_the_second_stage(tmp_path):
    from gem_agent.graph.build import build_graph
    from gem_agent.graph.checkpoint import checkpointer

    planner, judge = FakePlanner(), FakeJudge()
    searcher = CrashInRest([[bid(1), bid(2, closing_day=21)]], rest=[[bid(2, closing_day=21), bid(3, closing_day=22)]])
    s = services(tmp_path, planner=planner, searcher=searcher, judge=judge, checkpoint=tmp_path / "checkpoints.sqlite")
    manager = RunManager(s)
    events = await ask(manager, "LOS tenders")
    run_id = events[0]["run"]["run_id"]
    assert events[-1] == {"type": "done", "status": "failed"}

    async with checkpointer(s.checkpoint_path) as saver:  # what the run left behind
        saved = await build_graph(s).compile(checkpointer=saver).aget_state({"configurable": {"thread_id": run_id}})
    assert saved.next == ("search_rest",)
    assert saved.values["stage"] == "first" and saved.values["done_ids"] == [1, 2]
    assert saved.values["search_progress"][LOS.key()]["fake"].pages_read == 1

    searcher.stages.clear()
    judged_before = list(judge.judged)
    await manager.resume(run_id)
    assert searcher.stages == ["rest"] and len(planner.notes) == 1  # neither planned nor searched again
    assert judged_before == [1, 2] and judge.judged[len(judged_before):] == [3]
    assert s.store.load(run_id).status == "complete"
    assert sorted(manager.view("asha", run_id)["results"], key=lambda b: b["b_id"])[2]["b_id"] == 3


# --- cards after the run ---------------------------------------------------------------------------------------------

VAGUE = "Hiring of Agency for IT Projects- Milestone basis"


class Cards:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    async def __call__(self, bids):
        self.calls.append(list(bids))
        if self.fail:
            raise RuntimeError("model down")


async def test_cards_are_written_for_vague_bids_collected_in_the_run(tmp_path):
    vague_bid, failed_vague = bid(2, VAGUE, 18), bid(4, VAGUE, 19)
    cards = Cards()
    s = services(tmp_path, searcher=FakeSearcher([[bid(1), vague_bid, failed_vague]]),
                 collector=FakeCollector(fail={4}), cards=cards)
    events = await ask(RunManager(s), "LOS tenders")
    assert cards.calls == [[vague_bid]]  # bid 1 has a plain title; bid 4 collected nothing
    assert events[-1] == {"type": "done", "status": "complete"}


async def test_no_cards_are_asked_for_when_no_vague_bid_was_collected(tmp_path):
    cards = Cards()
    s = services(tmp_path, cards=cards)
    await ask(RunManager(s), "LOS tenders")
    assert cards.calls == []


async def test_a_card_failure_does_not_fail_the_run(tmp_path):
    cards = Cards(fail=True)
    s = services(tmp_path, searcher=FakeSearcher([[bid(2, VAGUE)]]), cards=cards)
    events = await ask(RunManager(s), "LOS tenders")
    final_message = [e["event"]["text"] for e in events if e["type"] == "progress"][-1]
    assert cards.calls and final_message.startswith("Done:")
    assert events[-1] == {"type": "done", "status": "complete"}
