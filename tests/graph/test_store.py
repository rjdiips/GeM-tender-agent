import json
from datetime import datetime

from gem_agent.graph.store import Outcome, Ruling, RunStore
from tests.graph.fakes import bid


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
