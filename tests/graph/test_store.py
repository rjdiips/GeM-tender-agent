import json
from datetime import datetime

from gem_agent.graph.store import Outcome, ProcessEvent, ProcessProgress, Ruling, RunStore
from tests.agents.helpers import LOS
from tests.graph.fakes import FakeJudge, bid


def outcome(b_id):
    return Outcome(b_id=b_id, group="g", bid=bid(b_id))


def test_update_outcomes_rereads_inside_the_lock(tmp_path):
    store = RunStore(tmp_path)
    store.save_outcome("r", outcome(1))
    store.outcomes("r")  # an endpoint read before its model calls
    store.save_outcome("r", outcome(2))  # the run saved another bid meanwhile

    def rule(all_):
        ruling = Ruling(decision="not", by="you", fact_id="f", fact="x", at=datetime(2026, 11, 18))
        all_[1] = all_[1].model_copy(update={"ruling": ruling})
        return [1]

    changed = store.update_outcomes("r", rule)
    assert set(store.outcomes("r")) == {1, 2}
    assert set(changed) == {1} and changed[1].ruling.decision == "not"
    assert store.outcomes("r")[1].ruling.created_fact is False
    assert (tmp_path / "r" / "outcomes.lock").exists()


def test_old_outcomes_load_without_ruling_or_undone(tmp_path):
    store = RunStore(tmp_path)
    path = tmp_path / "r" / "outcomes.json"
    path.parent.mkdir()
    text = json.dumps({"1": outcome(1).model_dump(mode="json")}, indent=1)
    path.write_text(text.replace('"ruling": null,', "").replace('"undone": false', '"x": 0'))
    o = store.outcomes("r")[1]
    assert o.ruling is None and o.undone is False


def test_outcomes_stored_before_these_fields_still_load(tmp_path):
    store = RunStore(tmp_path)
    path = tmp_path / "r" / "outcomes.jsonl"
    path.parent.mkdir()
    row = outcome(1).model_dump(mode="json")
    row.pop("read_because", None)
    row.pop("not_read", None)
    row.pop("topic_key", None)
    path.write_text(json.dumps(row) + "\n")
    o = store.outcomes("r")[1]
    assert o.topic_key is None and o.read_because is None and o.not_read == ""


def test_read_because_and_not_read_survive_a_round_trip(tmp_path):
    store = RunStore(tmp_path)
    store.save_outcome("r", Outcome(b_id=1, group="g", bid=bid(1), read_because="match"))
    store.save_outcome("r", Outcome(b_id=2, group="g", bid=bid(2), not_read="Not a match."))
    outcomes = store.outcomes("r")
    assert outcomes[1].read_because == "match" and outcomes[1].not_read == ""
    assert outcomes[2].read_because is None and outcomes[2].not_read == "Not a match."


def test_save_outcome_appends_one_line(tmp_path):
    store = RunStore(tmp_path)
    store.save_outcome("r", outcome(1))
    store.save_outcome("r", outcome(2))
    assert len((tmp_path / "r" / "outcomes.jsonl").read_text().splitlines()) == 2
    assert not (tmp_path / "r" / "outcomes.json").exists()


def test_last_line_wins(tmp_path):
    store = RunStore(tmp_path)
    store.save_outcome("r", outcome(1))
    store.save_outcome("r", outcome(1).model_copy(update={"restored": True}))
    assert store.outcomes("r")[1].restored


def test_old_outcomes_json_still_read(tmp_path):
    store = RunStore(tmp_path)
    path = tmp_path / "r" / "outcomes.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"1": outcome(1).model_dump(mode="json")}, indent=1))
    store.save_outcome("r", outcome(2))
    assert set(store.outcomes("r")) == {1, 2}
    assert json.loads(path.read_text()).keys() == {"1"}


def test_truncated_last_line_is_skipped(tmp_path, caplog):
    store = RunStore(tmp_path)
    store.save_outcome("r", outcome(1))
    with (tmp_path / "r" / "outcomes.jsonl").open("a") as f:
        f.write('{"b_id": 7, "gro')  # a crash part-way through a write
    assert list(store.outcomes("r")) == [1]
    assert "run r: skipping unreadable line 2 of outcomes.jsonl" in caplog.text
    store.save_outcome("r", outcome(2))  # a save after the crash keeps its own line
    assert set(store.outcomes("r")) == {1, 2}


def test_line_cut_inside_a_multibyte_character_is_skipped(tmp_path, caplog):
    store = RunStore(tmp_path)
    store.save_outcome("r", outcome(1))
    cut = '{"b_id": 7, "group": "g", "title": "टेंडर'.encode()
    with (tmp_path / "r" / "outcomes.jsonl").open("ab") as f:
        f.write(cut + "ं".encode()[:-1])  # the crash lands inside the last character's bytes
    assert list(store.outcomes("r")) == [1]
    assert "run r: skipping unreadable line 2 of outcomes.jsonl" in caplog.text
    store.save_outcome("r", outcome(2))
    assert set(store.outcomes("r")) == {1, 2}


def test_a_record_with_a_line_separator_in_its_text_is_read_whole(tmp_path, caplog):
    store = RunStore(tmp_path)
    store.save_outcome("r", outcome(1))
    store.save_outcome("r", Outcome(b_id=2, group="g", bid=bid(2, "Lot one\u2028Lot two\u2029end\x85")))
    assert store.outcomes("r")[2].bid.title == "Lot one\u2028Lot two\u2029end\x85"
    assert caplog.text == ""


def test_process_progress_round_trips_a_pending_read_verdict(tmp_path):
    verdict = FakeJudge()._verdict(LOS, bid(1), False, "judge", borderline=True)
    pending = Outcome(b_id=1, group="g", bid=bid(1), verdict=verdict, read_because="unsure")
    progress = ProcessProgress(done_ids=[1], described=1, reads={"g": 1}, calls={LOS.key(): {1: "unsure"}},
                               pending_reads={1: pending})
    store = RunStore(tmp_path)

    store.save_process_progress("r", progress)

    assert store.process_progress("r") == progress
    assert store.process_progress("r").pending_reads[1].verdict == verdict


def test_process_progress_round_trips_calls_per_topic(tmp_path):
    other = LOS.model_copy(update={"meaning": "Line of Sight: radio surveys"})
    progress = ProcessProgress(calls={LOS.key(): {1: "match"}, other.key(): {1: "no"}},
                               routes={LOS.key(): {1: "net"}, other.key(): {1: "match"}})
    store = RunStore(tmp_path)

    store.save_process_progress("r", progress)

    assert store.process_progress("r").calls == progress.calls
    assert store.process_progress("r").routes == progress.routes


def test_process_events_replay_incrementally(tmp_path):
    verdict = FakeJudge()._verdict(LOS, bid(1), False, "judge", borderline=True)
    pending = Outcome(b_id=1, group="g", bid=bid(1), verdict=verdict, read_because="unsure")
    store = RunStore(tmp_path)
    events = [
        ProcessEvent(kind="done", b_id=1),
        ProcessEvent(kind="described", number=2),
        ProcessEvent(kind="read", topic_key=LOS.key()),
        ProcessEvent(kind="call", topic_key=LOS.key(), b_id=1, call="match"),
        ProcessEvent(kind="route", topic_key=LOS.key(), b_id=1, route="match"),
        ProcessEvent(kind="cut", number=3),
        ProcessEvent(kind="pending", outcome=pending),
    ]
    for event in events:
        store.append_process_event("r", event)

    progress = store.process_progress("r")

    assert progress.done_ids == [1]
    assert progress.described == 2
    assert progress.reads == {LOS.key(): 1}
    assert progress.calls == {LOS.key(): {1: "match"}}
    assert progress.routes == {LOS.key(): {1: "match"}}
    assert progress.cut == 3
    assert progress.pending_reads == {1: pending}
    assert not (tmp_path / "r" / "process.json").exists()

    store.append_process_event("r", ProcessEvent(kind="pending_clear", b_id=1))
    assert store.process_progress("r").pending_reads == {}


def test_process_progress_reads_legacy_bid_only_calls_without_sharing_them(tmp_path):
    path = tmp_path / "r" / "process.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"done_ids": [], "described": 0, "reads": {}, "calls": {"1": "match"}, "cut": 0}))

    progress = RunStore(tmp_path).process_progress("r")

    assert progress.calls == {}
    assert progress.legacy_calls == {1: "match"}


def test_outcome_topic_key_survives_a_round_trip(tmp_path):
    store = RunStore(tmp_path)
    store.save_outcome("r", Outcome(b_id=1, group="LOS", topic_key=LOS.key(), bid=bid(1)))
    assert store.outcomes("r")[1].topic_key == LOS.key()


def test_process_progress_from_before_pending_reads_still_loads(tmp_path):
    path = tmp_path / "r" / "process.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"done_ids": [1], "described": 1, "reads": {"g": 1}, "calls": {"1": "match"},
                                "cut": 0}))

    progress = RunStore(tmp_path).process_progress("r")

    assert progress.done_ids == [1] and progress.described == 1 and progress.reads == {"g": 1}
    assert progress.pending_reads == {}
