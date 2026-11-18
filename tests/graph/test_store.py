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
    store.save_outcome("r", outcome(1))
    path = tmp_path / "r" / "outcomes.json"
    path.write_text(path.read_text().replace('"ruling": null,', "").replace('"undone": false', '"x": 0'))
    o = store.outcomes("r")[1]
    assert o.ruling is None and o.undone is False
