import asyncio
import json

import pytest

from gem_agent.agents.collector import DocumentUnavailable
from gem_agent.agents.rulings import RulingMatch
from gem_agent.graph.build import skipped_line
from gem_agent.graph.events import EventBus
from gem_agent.graph.manager import RunManager
from gem_agent.graph.services import Services
from gem_agent.graph.store import RunStore
from gem_agent.memory.user_memory import MemoryStore
from gem_agent.tools.gem.live import SearchProgress
from tests.agents.helpers import LOS
from tests.graph.fakes import (
    NOW,
    BlockingCollector,
    FakeChat,
    FakeCollector,
    FakeDescriber,
    FakeJudge,
    FakeMatcher,
    FakePlanner,
    FakeRulings,
    FakeSearcher,
    bid,
)

SIGHT = LOS.model_copy(update={"meaning": "Line of Sight: radio survey for telecom towers"})


def services(tmp_path, *, chat=None, searcher=None, collector=None, judge=None, planner=None, rulings=None,
             checkpoint=":memory:", describer=None, matcher=None, safety_net=True, describe_cap=300,
             similarity_embedder=None, workers=4):
    s = Services(chat=chat or FakeChat(), planner=planner or FakePlanner(),
                 searcher=searcher or FakeSearcher([[bid(1), bid(2, "Lockers", 18)]]),
                 collector=collector or FakeCollector(), judge=judge or FakeJudge(),
                 store=RunStore(tmp_path / "runs"), bus=EventBus(), memory=MemoryStore(tmp_path), now=lambda: NOW,
                 checkpoint_path=checkpoint, rulings=rulings)
    s.describer, s.matcher = describer, matcher
    s.safety_net, s.describe_cap = safety_net, describe_cap
    s.similarity_embedder, s.workers = similarity_embedder, workers
    return s


async def ask(manager, text, run_id=None, user="asha"):
    return [e async for e in manager.ask(user, text, run_id=run_id)]


async def test_a_search_streams_understanding_progress_and_each_result(tmp_path):
    s = services(tmp_path, judge=FakeJudge(no={2}))
    events = await ask(RunManager(s), "LOS tenders published in the last 2 weeks")
    types = [e["type"] for e in events]
    assert types[0] == "started" and types[1] == "understood" and types[-1] == "done"
    assert types.count("started") == 1 and types.count("understood") == 1
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


async def test_without_the_new_services_title_drops_still_take_exactly_todays_path(tmp_path):
    searcher = FakeSearcher([[]], dropped=[[bid(3)]], more_pages=False)
    s = services(tmp_path, searcher=searcher)
    events = await ask(RunManager(s), "LOS tenders")
    run_id = events[0]["run"]["run_id"]

    assert s.store.outcomes(run_id) == {}
    assert texts_of(events)[-1].startswith("No matching tenders found ")


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
    assert "window_count" not in view["understood"] and view["understood"]["likely"] == 3
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


class UnreadableCollector(FakeCollector):
    async def collect(self, bid, **kw):
        if bid.b_id in self.fail:
            self.calls.append((bid.b_id, kw.get("attachments", True)))
            raise DocumentUnavailable("GeM returned 503 three times")
        return await super().collect(bid, **kw)


async def test_unreadable_bid_shows_as_error_not_triage(tmp_path):
    s = services(tmp_path, collector=UnreadableCollector(fail={2}))
    events = await ask(RunManager(s), "LOS tenders")
    run_id = events[0]["run"]["run_id"]
    outcome = s.store.outcomes(run_id)[2]
    assert outcome.error.startswith("bid document could not be downloaded") and outcome.verdict is None


# --- descriptions, matching and routing ---------------------------------------------------------------------------


async def test_every_pool_bid_is_described_once_and_matched(tmp_path):
    describer, matcher = FakeDescriber(), FakeMatcher()
    searcher = FakeSearcher([[bid(1, closing_day=20), bid(2, closing_day=21)]],
                           dropped=[[bid(3, closing_day=19)]], more_pages=False)
    s = services(tmp_path, searcher=searcher, describer=describer, matcher=matcher)
    events = await ask(RunManager(s), "LOS tenders")

    assert describer.described == [1, 2, 3]  # kept bids first, then closing order within each kind
    assert matcher.batches == [[1, 2, 3]]
    assert "Described 3 new bids (0 already known)." in texts_of(events)
    assert "Matched 3 bids: 3 match, 0 might." in texts_of(events)


async def test_a_bid_the_description_matches_is_read_even_when_the_title_screen_dropped_it(tmp_path):
    describer, matcher, judge = FakeDescriber(), FakeMatcher(), FakeJudge()
    searcher = FakeSearcher([[bid(1, closing_day=25)]], dropped=[[bid(3, closing_day=19)]], more_pages=False)
    s = services(tmp_path, searcher=searcher, describer=describer, matcher=matcher, judge=judge)
    events = await ask(RunManager(s), "LOS tenders")

    assert 3 in judge.judged
    result = await result_of(events, 3)
    assert result["state"] == "relevant" and result["read_because"] == "match"


async def test_a_run_with_only_title_dropped_hits_reports_the_match_it_rescued(tmp_path):
    judge = FakeJudge()
    s = services(tmp_path, searcher=FakeSearcher([[]], dropped=[[bid(3)]], more_pages=False), judge=judge,
                 describer=FakeDescriber(), matcher=FakeMatcher())
    events = await ask(RunManager(s), "LOS tenders")

    assert judge.judged == [3]
    assert texts_of(events)[-1] == "Done: 1 relevant, 0 quarantined."


async def test_a_no_from_both_checks_quarantines_without_reading(tmp_path):
    describer, matcher, judge = FakeDescriber({3: "Office lockers"}), FakeMatcher({3: "no"}), FakeJudge()
    searcher = FakeSearcher([[bid(1)]], dropped=[[bid(3)]], more_pages=False)
    s = services(tmp_path, searcher=searcher, describer=describer, matcher=matcher, judge=judge)
    events = await ask(RunManager(s), "LOS tenders")

    assert judge.judged == [1]
    result = await result_of(events, 3)
    assert result["state"] == "quarantined" and result["read_because"] is None
    assert result["reason"] == ("Not a match: buys Office lockers. "
                                "Ruled out by the description check and the title check.")


async def test_the_safety_net_reads_a_no_the_title_screen_kept(tmp_path):
    collector, judge = FakeCollector(), FakeJudge()
    s = services(tmp_path, searcher=FakeSearcher([[bid(1)]], more_pages=False), collector=collector,
                 judge=judge, describer=FakeDescriber(), matcher=FakeMatcher({1: "no"}))
    events = await ask(RunManager(s), "LOS tenders")

    assert (1, False) in collector.calls and collector.calls.count((1, True)) == 2
    assert judge.judged == [1]
    result = await result_of(events, 1)
    assert result["state"] == "relevant" and result["read_because"] == "net"


async def test_a_net_bid_that_triage_rules_out_is_quarantined_with_both_checks(tmp_path):
    collector, judge = FakeCollector(), FakeJudge(no={1})
    s = services(tmp_path, searcher=FakeSearcher([[bid(1)]], more_pages=False), collector=collector,
                 judge=judge, describer=FakeDescriber({1: "Office lockers"}), matcher=FakeMatcher({1: "no"}))
    events = await ask(RunManager(s), "LOS tenders")

    assert collector.calls == [(1, True), (1, False)] and judge.judged == []
    result = await result_of(events, 1)
    assert result["state"] == "quarantined" and result["stage"] == "triage"
    assert result["reason"] == ("Not a match: buys Office lockers. "
                                "Ruled out by the description check and the first-pages check.")


async def test_with_the_net_off_a_no_with_scope_drops_without_triage(tmp_path):
    collector, judge = FakeCollector(), FakeJudge()
    s = services(tmp_path, searcher=FakeSearcher([[bid(1)]], more_pages=False), collector=collector,
                 judge=judge, describer=FakeDescriber({1: "Office lockers"}), matcher=FakeMatcher({1: "no"}),
                 safety_net=False)
    events = await ask(RunManager(s), "LOS tenders")

    assert collector.calls == [(1, True)] and judge.judged == []
    result = await result_of(events, 1)
    assert result["state"] == "quarantined"
    assert result["reason"] == "Not a match: buys Office lockers. Ruled out by the description check."


async def test_reads_go_match_then_unsure_then_net(tmp_path):
    judge = FakeJudge()
    searcher = FakeSearcher([[bid(1, closing_day=25), bid(2, closing_day=24), bid(3, closing_day=23)]],
                           more_pages=False)
    s = services(tmp_path, searcher=searcher, judge=judge, describer=FakeDescriber(),
                 matcher=FakeMatcher({1: "match", 2: "unsure", 3: "no"}), workers=1)

    await ask(RunManager(s), "LOS tenders")

    assert judge.judged == [1, 2, 3]


async def test_net_and_today_share_one_closing_order_bucket(tmp_path):
    judge = FakeJudge()
    searcher = FakeSearcher([[bid(1, closing_day=25), bid(2, closing_day=24), bid(3, closing_day=23),
                              bid(4, closing_day=20)]], more_pages=False)
    s = services(tmp_path, searcher=searcher, judge=judge, describer=FakeDescriber({4: ""}),
                 matcher=FakeMatcher({1: "match", 2: "unsure", 3: "no"}), workers=1)

    await ask(RunManager(s), "LOS tenders")

    assert judge.judged == [1, 2, 4, 3]


async def test_new_descriptions_are_capped_per_request_and_the_rest_take_todays_path(tmp_path):
    describer, matcher = FakeDescriber(), FakeMatcher()
    searcher = FakeSearcher([[bid(1, closing_day=18), bid(2, closing_day=19), bid(3, closing_day=20)]],
                           more_pages=False)
    s = services(tmp_path, searcher=searcher, describer=describer, matcher=matcher, describe_cap=2)
    events = await ask(RunManager(s), "LOS tenders")

    assert describer.described == [1, 2] and matcher.batches == [[1, 2]]
    result = await result_of(events, 3)
    assert result["state"] == "relevant" and result["read_because"] is None
    assert "1 bid not yet described; they will be checked on the next search." in texts_of(events)


async def test_the_description_cap_is_shared_across_both_stages(tmp_path):
    describer, matcher = FakeDescriber(), FakeMatcher()
    searcher = FakeSearcher([[bid(1)]], rest=[[bid(2)]])
    s = services(tmp_path, searcher=searcher, describer=describer, matcher=matcher, describe_cap=1)
    events = await ask(RunManager(s), "LOS tenders")

    assert describer.described == [1] and matcher.batches == [[1]]
    assert (await result_of(events, 2))["read_because"] is None
    assert "1 bid not yet described; they will be checked on the next search." in texts_of(events)


async def test_the_description_cap_is_one_queue_across_topics(tmp_path):
    describer, matcher = FakeDescriber(), FakeMatcher()
    searcher = FakeSearcher([[bid(1, closing_day=20)], [bid(5, "Line of sight survey", closing_day=18)]])
    s = services(tmp_path, chat=FakeChat(topics=[LOS, SIGHT]), searcher=searcher,
                 describer=describer, matcher=matcher, describe_cap=1)
    events = await ask(RunManager(s), "LOS tenders")

    assert describer.described == [5] and matcher.batches == [[5]]
    assert (await result_of(events, 1))["read_because"] is None
    assert "1 bid not yet described; they will be checked on the next search." in texts_of(events)


async def test_match_batches_flush_as_each_forty_descriptions_become_ready(tmp_path):
    describer, matcher = FakeDescriber(), FakeMatcher()
    bids = [bid(i) for i in range(1, 42)]
    s = services(tmp_path, searcher=FakeSearcher([bids], more_pages=False), describer=describer, matcher=matcher)

    await ask(RunManager(s), "LOS tenders")

    assert matcher.batches == [list(range(1, 41)), [41]]


async def test_the_read_cap_counts_across_both_stages_and_reports_what_it_cuts(tmp_path):
    describer, matcher, judge = FakeDescriber(), FakeMatcher(), FakeJudge()
    searcher = FakeSearcher([[bid(1), bid(2)]], rest=[[bid(3), bid(4), bid(5)]], cap=3)
    s = services(tmp_path, searcher=searcher, describer=describer, matcher=matcher, judge=judge)
    events = await ask(RunManager(s), "LOS tenders")

    assert searcher.caps == [None, 1]
    assert describer.described == [1, 2, 3, 4, 5]
    assert matcher.batches == [[1, 2], [3, 4, 5]]
    assert judge.judged == [1, 2, 3]
    progress = texts_of(events)
    assert progress.count("2 more matching bids not read; narrow the request to see them.") == 1
    assert progress[-1] == ("Done: 3 relevant, 0 quarantined. 2 more matching bids not read; "
                            "narrow the request to see them.")


async def test_a_bid_cut_by_the_read_cap_is_not_matched_again_in_stage_two(tmp_path):
    describer, matcher, judge = FakeDescriber(), FakeMatcher(), FakeJudge()
    searcher = FakeSearcher([[bid(1)]], dropped=[[bid(2), bid(3)]], rest=[[bid(4)]], cap=2)
    s = services(tmp_path, searcher=searcher, describer=describer, matcher=matcher, judge=judge)

    await ask(RunManager(s), "LOS tenders")

    assert matcher.batches == [[1, 2, 3], [4]]
    assert judge.judged == [1, 2]


async def test_a_bid_whose_documents_fail_while_described_is_an_error(tmp_path):
    collector = UnreadableCollector(fail={1})
    describer, matcher, judge = FakeDescriber(), FakeMatcher(), FakeJudge()
    s = services(tmp_path, searcher=FakeSearcher([[bid(1)]], more_pages=False), collector=collector,
                 describer=describer, matcher=matcher, judge=judge)
    events = await ask(RunManager(s), "LOS tenders")
    run_id = events[0]["run"]["run_id"]

    outcome = s.store.outcomes(run_id)[1]
    assert outcome.error.startswith("bid document could not be downloaded") and outcome.verdict is None
    assert describer.described == [] and matcher.batches == [] and judge.judged == []
    assert collector.calls == [(1, True)]


async def test_an_undescribed_title_drop_is_not_retried_in_the_same_request(tmp_path):
    class FailOnce(FakeDescriber):
        def __init__(self):
            super().__init__()
            self.attempts: list[int] = []

        async def describe(self, bid, files):
            self.attempts.append(bid.b_id)
            if bid.b_id == 3 and self.attempts.count(3) == 1:
                return None
            return await super().describe(bid, files)

    describer, matcher = FailOnce(), FakeMatcher()
    searcher = FakeSearcher([[bid(1)]], dropped=[[bid(3)]], rest=[[bid(4)]])
    s = services(tmp_path, searcher=searcher, describer=describer, matcher=matcher)
    events = await ask(RunManager(s), "LOS tenders")
    run_id = events[0]["run"]["run_id"]

    assert describer.attempts == [1, 3, 4]
    assert describer.described == [1, 4] and matcher.batches == [[1], [4]]
    assert 3 not in s.store.outcomes(run_id)


async def test_the_second_stage_does_not_search_or_describe_pool_rest_bids_again(tmp_path):
    describer, matcher = FakeDescriber(), FakeMatcher()
    searcher = FakeSearcher([[bid(1)]], dropped=[[bid(3)]], rest=[[bid(3), bid(4)]])
    s = services(tmp_path, searcher=searcher, describer=describer, matcher=matcher)

    await ask(RunManager(s), "LOS tenders")

    assert searcher.excluded == [{1, 3}]
    assert describer.described == [1, 3, 4]
    assert matcher.batches == [[1, 3], [4]]


async def test_reading_reports_a_running_count_from_the_start_to_the_last_bid(tmp_path):
    bids = [bid(i, closing_day=18 + i % 5) for i in range(1, 21)]
    s = services(tmp_path, searcher=FakeSearcher([bids]), judge=FakeJudge(no={3, 4, 5}), collector=FakeCollector(fail={7}))
    events = await ask(RunManager(s), "LOS tenders")
    progress = [e["event"] for e in events if e["type"] == "progress"]
    texts = [p["text"] for p in progress]
    counts = [p["text"] for p in progress if p.get("kind") == "count"]
    assert texts[0] == "Searching GeM 5 ways."  # 4 base searches + the plan's one term
    assert texts[1].startswith("Shortlist: 20 bids")
    assert texts[2] == counts[0] == "Read 0 of 20 bids: 0 relevant, 0 not a match."  # at once
    assert len(counts) == 21  # then after every bid; the chat shows only the newest
    # 16 judged bids x 2 files + 3 triage rejections x 1 (bid document only); bid 7 failed
    assert counts[-1] == "Read 20 of 20 bids: 16 relevant, 3 not a match, 1 could not be read."
    assert texts[-3:] == [counts[-1], "Looking through more GeM pages for older matches.",  # stage 2 is not silent
                          "Done: 16 relevant, 3 quarantined, 1 could not be read."]


async def test_process_reads_outcomes_once(tmp_path):
    """The running count is counted from what process holds, not re-read from disk after every bid."""
    bids = [bid(i, closing_day=18 + i % 5) for i in range(1, 11)]
    collector = FakeCollector()
    s = services(tmp_path, searcher=FakeSearcher([bids], more_pages=False), collector=collector)
    read_outcomes = s.store.outcomes
    reads = 0

    def counted(run_id):
        nonlocal reads
        reads += 1
        return read_outcomes(run_id)

    s.store.outcomes = counted
    collect, seen = collector.collect, []

    async def spied(b, **kwargs):
        seen.append(reads)
        return await collect(b, **kwargs)

    collector.collect = spied
    await ask(RunManager(s), "LOS tenders")
    assert len(seen) == 20 and len(set(seen)) == 1  # every bid's collect saw the same number of reads


def test_every_bid_read_is_in_the_running_count():
    from gem_agent.graph.build import running_count
    from gem_agent.graph.store import Outcome
    judge, topic = FakeJudge(), LOS

    def outcome(b_id, relevant, stage, error=""):
        v = judge._verdict(topic, bid(b_id), relevant, stage)
        return Outcome(b_id=b_id, group="LOS", bid=bid(b_id), verdict=None if error else v, error=error)
    line = running_count(4, 9, [outcome(1, True, "judge"), outcome(2, False, "triage"), outcome(3, False, "judge"),
                                outcome(4, False, "judge", error="503")])
    assert line == "Read 4 of 9 bids: 1 relevant, 2 not a match, 1 could not be read."


def test_running_count_counts_bids_not_read_as_not_a_match():
    from gem_agent.graph.build import running_count
    from gem_agent.graph.store import Outcome

    o = Outcome(b_id=1, group="LOS", bid=bid(1), not_read="Not a match: buys Lockers.")
    assert running_count(1, 1, [o]) == "Read 1 of 1 bids: 0 relevant, 1 not a match."


async def test_report_counts_a_bid_that_was_not_read(tmp_path):
    from gem_agent.agents.schemas import TenderRequest
    from gem_agent.agents.searcher import Candidate
    from gem_agent.graph.build import build_graph
    from gem_agent.graph.state import RunState
    from gem_agent.graph.store import Outcome
    from tests.graph.fakes import WINDOW

    s = services(tmp_path)
    s.store.save_outcome("r", Outcome(b_id=1, group="LOS", bid=bid(1), not_read="Not a match: buys Lockers."))
    state = RunState(run_id="r", user="asha", request=TenderRequest(intent="search", user_text="LOS tenders",
                                                                   topics=[LOS], window=WINDOW),
                     candidates=[Candidate(bid=bid(1), group="LOS", why=["title words"])])
    await build_graph(s).nodes["report"].runnable.ainvoke(state)
    assert s.store.events("r")[-1]["event"]["text"] == "Done: 0 relevant, 1 quarantined."


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
    assert texts[0] == "Searching GeM 5 ways."
    assert texts[1].startswith("Shortlist: 2 bids")  # kept, not swallowed by the meaning card


def test_count_lines_sent_without_a_kind_are_marked_as_counts():
    from gem_agent.graph.views import _progress
    old = {"at": "2026-11-17T15:22:27", "text": "Read 4 of 33 bids: 31 files downloaded, triage dropped 1…"}
    done = {"at": "2026-11-17T15:24:32", "text": "Done: 9 relevant, 24 quarantined."}
    assert _progress([{"type": "progress", "event": old}, {"type": "progress", "event": done}]) == [
        {**old, "kind": "count"}, done]


Q = "Does bid 2 count for you?"
TWO = lambda: [[bid(1), bid(2, "Lockers", 18)]]


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


# --- two stages: judge page 1, then search deeper ---------------------------------------------------------


async def test_a_run_judges_page_one_bids_then_searches_deeper_and_judges_the_new_ones(tmp_path):
    searcher, judge = FakeSearcher([[bid(1), bid(2, closing_day=21)]], rest=[[bid(2, closing_day=21), bid(3, closing_day=19)]]), FakeJudge()
    s = services(tmp_path, searcher=searcher, judge=judge)
    events = await ask(RunManager(s), "LOS tenders")
    assert searcher.stages == ["first", "rest"] and judge.judged == [1, 2, 3]
    assert searcher.excluded == [{1, 2}]  # every bid already in the run
    assert [e["bid"]["b_id"] for e in events if e["type"] == "result"] == [1, 2, 3]
    progress = [e["event"]["text"] for e in events if e["type"] == "progress"]
    assert "Found 1 more bid to read." in progress
    live = [e["event"]["text"] for e in events if e["type"] == "progress" and e["event"].get("phase") == "search_rest"
            and e["event"].get("kind") == "count"]
    assert live == ["More GeM pages checked: 2 titles so far."]  # the deeper pages' live count: 3 x the 148 places left
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
    assert "Found 1 more bid to read." in [e["event"]["text"] for e in events if e["type"] == "progress"]


async def test_no_deeper_line_when_the_second_stage_finds_nothing_new(tmp_path):
    s = services(tmp_path, searcher=FakeSearcher([[bid(1)]], rest=[[bid(1)]]))
    events = await ask(RunManager(s), "LOS tenders")
    assert not any(e["event"]["text"].startswith("Found ") for e in events if e["type"] == "progress")
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
SKIPPED = SearchProgress(num_found=25000, pages_read=1, page_count=2500, skipped=True)


def test_skipped_line():
    assert skipped_line(["software"]) == "Skipped “software”: it matches over 20,000 open bids, too broad to read."
    assert skipped_line(["software", "systems"]) == (
        "Skipped “software”, “systems”: they match over 20,000 open bids, too broad to read.")


async def test_skipped_search_is_reported(tmp_path):
    searcher = FakeSearcher([[bid(1)]], searches={"first": {"fullText|software": SKIPPED}})
    s = services(tmp_path, searcher=searcher)
    events = await ask(RunManager(s), "LOS tenders")
    progress = [e["event"]["text"] for e in events if e["type"] == "progress"]
    assert "Skipped “software”: it matches over 20,000 open bids, too broad to read." in progress


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
    assert "2 more matching bids not read; narrow the request to see them." in progress
    assert progress[-1] == ("Done: 3 relevant, 0 quarantined. 2 more matching bids not read; "
                            "narrow the request to see them.")


async def test_an_over_cap_retry_that_shrinks_nothing_is_not_repeated(tmp_path):
    planner = FakePlanner()
    searcher = FakeSearcher([[bid(1), bid(2), bid(3)]], rest=[[bid(4)]], cap=2)  # 3 bids every round: no shrink
    s = services(tmp_path, planner=planner, searcher=searcher)
    events = await ask(RunManager(s), "LOS tenders")
    assert len(planner.notes) == 2 and "more specific" in planner.notes[1]  # narrowed once, then read
    assert searcher.stages == ["first", "first"]  # stage 1 used the cap up: stage 2 has no pages to read
    progress = [e["event"]["text"] for e in events if e["type"] == "progress"]
    assert progress.count("1 more matching bid not read; narrow the request to see them.") == 1  # stage 1's cut
    assert progress[-1].endswith(" 1 more matching bid not read; narrow the request to see them.")


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


async def test_a_resumed_run_does_not_describe_or_match_completed_bids_again(tmp_path):
    describer, matcher, judge = FakeDescriber(), FakeMatcher(), FakeJudge()
    searcher = CrashInRest([[bid(1), bid(2, closing_day=21)]], rest=[[bid(2, closing_day=21), bid(3, closing_day=22)]])
    s = services(tmp_path, searcher=searcher, describer=describer, matcher=matcher, judge=judge,
                 checkpoint=tmp_path / "checkpoints.sqlite")
    manager = RunManager(s)
    events = await ask(manager, "LOS tenders")
    run_id = events[0]["run"]["run_id"]
    assert events[-1] == {"type": "done", "status": "failed"}
    assert describer.described == [1, 2] and matcher.batches == [[1, 2]]

    await manager.resume(run_id)

    assert describer.described == [1, 2, 3] and matcher.batches == [[1, 2], [3]]
    assert judge.judged == [1, 2, 3]
    assert s.store.load(run_id).status == "complete"


# --- progress lines in plain words: the copy in the table of the Task 9 brief -----------------------------------------


def texts_of(events):
    return [e["event"]["text"] for e in events if e["type"] == "progress"]


class HoldAllButFirst(FakeCollector):
    """The first bid is read; the others never finish, so a run can be stopped after one bid."""

    async def collect(self, bid, **kw):
        if bid.b_id != 1:
            await asyncio.Event().wait()
        return await super().collect(bid, **kw)


async def test_plan_line(tmp_path):
    s = services(tmp_path)
    assert texts_of(await ask(RunManager(s), "LOS tenders"))[0] == "Searching GeM 5 ways."


async def test_shortlist_line_singular_and_plural(tmp_path):
    one = await ask(RunManager(services(tmp_path / "one", searcher=FakeSearcher([[bid(1)]]))), "LOS tenders")
    assert "Shortlist: 1 bid to read from 3 titles checked, closing soonest first." in texts_of(one)
    two = await ask(RunManager(services(tmp_path / "two")), "LOS tenders")
    assert "Shortlist: 2 bids to read from 6 titles checked, closing soonest first." in texts_of(two)


async def test_search_rest_start_line(tmp_path):
    s = services(tmp_path)
    assert "Looking through more GeM pages for older matches." in texts_of(await ask(RunManager(s), "LOS tenders"))


async def test_search_rest_count_line(tmp_path):
    s = services(tmp_path, searcher=FakeSearcher([[bid(1)]], rest=[[bid(3), bid(4)]]))
    events = await ask(RunManager(s), "LOS tenders")
    counts = [e["event"] for e in events if e["type"] == "progress" and e["event"].get("kind") == "count"
              and e["event"].get("phase") == "search_rest"]
    assert counts[-1]["text"] == "More GeM pages checked: 2 titles so far."


async def test_search_rest_new_line_singular_and_plural(tmp_path):
    one = await ask(RunManager(services(tmp_path / "one", searcher=FakeSearcher([[bid(1)]], rest=[[bid(3)]]))), "LOS tenders")
    assert "Found 1 more bid to read." in texts_of(one)
    two = await ask(RunManager(services(tmp_path / "two", searcher=FakeSearcher([[bid(1)]], rest=[[bid(3), bid(4)]]))),
                    "LOS tenders")
    assert "Found 2 more bids to read." in texts_of(two)


def test_read_count_line_singular_and_plural():
    from gem_agent.graph.build import running_count
    from gem_agent.graph.store import Outcome
    judge = FakeJudge()

    def outcome(b_id, relevant, stage):
        return Outcome(b_id=b_id, group="LOS", bid=bid(b_id), verdict=judge._verdict(LOS, bid(b_id), relevant, stage))
    assert running_count(1, 1, [outcome(1, True, "judge")]) == "Read 1 of 1 bids: 1 relevant, 0 not a match."
    assert running_count(1, 1, [outcome(1, False, "triage")]) == "Read 1 of 1 bids: 0 relevant, 1 not a match."


def test_describe_and_match_lines():
    from gem_agent.graph.build import described_line, matched_line, undescribed_line

    assert described_line(1, 0) == "Described 1 new bid (0 already known)."
    assert described_line(12, 40) == "Described 12 new bids (40 already known)."
    assert matched_line(52, 6, 4) == "Matched 52 bids: 6 match, 4 might."
    assert matched_line(1, 1, 1) == "Matched 1 bid: 1 match, 1 might."
    assert undescribed_line(3) == "3 bids not yet described; they will be checked on the next search."
    assert undescribed_line(1) == "1 bid not yet described; they will be checked on the next search."


def test_cut_line_singular_and_plural():
    from gem_agent.graph.build import cut_line
    assert cut_line(1) == "1 more matching bid not read; narrow the request to see them."
    assert cut_line(2) == "2 more matching bids not read; narrow the request to see them."


async def test_no_progress_line_ends_with_dots(tmp_path):
    bids = [bid(i, closing_day=18 + i % 5) for i in range(1, 6)]
    s = services(tmp_path, searcher=FakeSearcher([bids], rest=[[bid(9)]]), judge=FakeJudge(no={3}))
    texts = texts_of(await ask(RunManager(s), "LOS tenders"))
    assert texts and not any(t.endswith("…") for t in texts)


async def test_stopped_run_gets_a_closing_line(tmp_path):
    s = services(tmp_path, collector=HoldAllButFirst())
    manager = RunManager(s)
    events = []

    async def follow():
        async for e in manager.ask("asha", "LOS tenders"):
            events.append(e)

    task = asyncio.create_task(follow())
    while not any(e["type"] == "result" for e in events):
        await asyncio.sleep(0.01)
    run_id = events[0]["run"]["run_id"]
    await manager.stop("asha", run_id)
    await asyncio.wait_for(task, timeout=5)
    last = texts_of(events)[-1]
    assert last.startswith("Stopped: read 1 of")
    assert last == "Stopped: read 1 of 2 bids, 1 relevant. The deeper search did not run."
    assert events[-1] == {"type": "done", "status": "stopped"}


async def test_failed_run_gets_a_closing_line(tmp_path):
    s = services(tmp_path, searcher=CrashOnce([[bid(1)]]))
    events = await ask(RunManager(s), "LOS tenders")
    assert texts_of(events)[-1] == "Stopped by an error after reading 0 of 0 bids. Try again; bids already read are kept."
    assert events[-1] == {"type": "done", "status": "failed"}


async def test_retry_lines_are_finished_steps_in_plain_words(tmp_path):
    widen = await ask(RunManager(services(tmp_path / "widen", searcher=FakeSearcher([[]], more_pages=False))),
                      "LOS tenders")
    narrow = await ask(RunManager(services(tmp_path / "narrow",
                                           searcher=FakeSearcher([[bid(1), bid(2), bid(3)]], rest=[[bid(4)]], cap=2))),
                       "LOS tenders")
    def retry_lines(events):
        return [e["event"]["text"] for e in events if e["type"] == "progress" and e["event"].get("phase") == "retry"]
    assert retry_lines(widen) == ["Nothing matched by title; widening the search."] * 2  # two widening rounds
    assert retry_lines(narrow) == ["Too many matches; narrowing the search."]
    for line in retry_lines(widen) + retry_lines(narrow):
        assert not line.endswith("…") and "triage" not in line and "placeholder" not in line
