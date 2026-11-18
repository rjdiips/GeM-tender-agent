import pytest

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
    FakeSearcher,
    bid,
)

SIGHT = LOS.model_copy(update={"meaning": "Line of Sight: radio survey for telecom towers"})


def services(tmp_path, *, chat=None, searcher=None, collector=None, judge=None, planner=None, checkpoint=":memory:"):
    return Services(chat=chat or FakeChat(), planner=planner or FakePlanner(),
                    searcher=searcher or FakeSearcher([[bid(1), bid(2, "Lockers", 18)]]),
                    collector=collector or FakeCollector(), judge=judge or FakeJudge(),
                    store=RunStore(tmp_path / "runs"), bus=EventBus(), memory=MemoryStore(tmp_path), now=lambda: NOW,
                    checkpoint_path=checkpoint)


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
    planner, searcher = FakePlanner(), FakeSearcher([[]])
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
    async def search(self, topic, plan):
        if self.calls == 0:
            self.calls += 1
            raise RuntimeError("process killed")
        return await super().search(topic, plan)


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
    assert texts[0].startswith("Shortlist: 20 bids")
    assert texts[1] == counts[0] == "Read 0 of 20 bids: 0 files downloaded, triage dropped 0, 0 relevant so far…"  # at once
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
    assert view["progress"][0]["text"].startswith("Shortlist: 2 bids")


def test_count_lines_sent_without_a_kind_are_marked_as_counts():
    from gem_agent.graph.views import _progress
    old = {"at": "2026-11-17T15:22:27", "text": "Read 4 of 33 bids: 31 files downloaded, triage dropped 1…"}
    done = {"at": "2026-11-17T15:24:32", "text": "Done: 9 relevant, 24 quarantined."}
    assert _progress([{"type": "progress", "event": old}, {"type": "progress", "event": done}]) == [
        {**old, "kind": "count"}, done]
