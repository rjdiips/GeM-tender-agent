from datetime import date

from gem_agent.agents.schemas import MemoryChange
from gem_agent.memory.user_memory import MemoryStore

D = date(2026, 11, 18)


def store(tmp_path):
    return MemoryStore(tmp_path)


def test_stated_fact_is_saved_at_once(tmp_path):
    s = store(tmp_path)
    s.add_stated("asha", "We sell AML and LOS software to banks", run_id="r1", today=D)
    [fact] = s.facts("asha")
    assert fact.source == "stated" and fact.active and fact.since == D


def test_learned_fact_needs_two_separate_runs(tmp_path):
    s = store(tmp_path)
    change = MemoryChange(action="add", fact="Interested in bank IT tenders", fact_id=None, evidence="kept 4 of 4 bank bids")
    s.apply("asha", [change], run_id="r1", today=D)
    assert s.prompt_facts("asha") == []
    s.apply("asha", [change], run_id="r1", today=D)  # same run twice still counts once
    assert s.prompt_facts("asha") == []
    s.apply("asha", [change.model_copy(update={"evidence": "kept 3 of 3"})], run_id="r2", today=D)
    [fact] = s.prompt_facts("asha")
    assert fact.source == "learned" and fact.runs == ["r1", "r2"]


def test_contradiction_weakens_then_removes(tmp_path):
    s = store(tmp_path)
    add = MemoryChange(action="add", fact="Only financial-sector buyers", fact_id=None, evidence="kept bank bids")
    s.apply("asha", [add], run_id="r1", today=D)
    s.apply("asha", [add], run_id="r2", today=D)
    fact_id = s.prompt_facts("asha")[0].id
    weaken = MemoryChange(action="weaken", fact=None, fact_id=fact_id, evidence="kept a hospital AML tender")
    s.apply("asha", [weaken], run_id="r3", today=D)
    assert s.prompt_facts("asha")[0].strength == 1
    s.apply("asha", [weaken], run_id="r4", today=D)
    assert s.prompt_facts("asha") == []


def test_forget_and_correct(tmp_path):
    s = store(tmp_path)
    s.add_stated("asha", "Ignore state-government tenders", run_id="r1", today=D)
    fid = s.facts("asha")[0].id
    s.correct("asha", fid, "Ignore state-government tenders outside Maharashtra", today=D)
    assert s.prompt_facts("asha")[0].fact.endswith("Maharashtra")
    s.forget("asha", fid)
    assert s.prompt_facts("asha") == []


def test_prompt_facts_newest_and_strongest_first_capped(tmp_path):
    s = store(tmp_path)
    for i in range(25):
        s.add_stated("asha", f"fact {i}", run_id=f"r{i}", today=date(2026, 11, 1 + i))
    facts = s.prompt_facts("asha", limit=20)
    assert len(facts) == 20 and facts[0].fact == "fact 24"


def test_users_are_separate(tmp_path):
    s = store(tmp_path)
    s.add_stated("asha", "x", run_id="r1", today=D)
    assert s.facts("ravi") == []
    assert (tmp_path / "users" / "asha" / "memory.json").exists()


def test_forget_everything(tmp_path):
    s = store(tmp_path)
    s.add_stated("asha", "x", run_id="r1", today=D)
    s.forget_all("asha")
    assert s.facts("asha") == []


def test_restated_fact_in_other_words_is_not_duplicated(tmp_path):
    # 16 Nov live run saved both of these
    s = store(tmp_path)
    s.add_stated("asha", "we're a banking software company", run_id="r1", today=D)
    s.add_stated("asha", "We are a banking software company.", run_id="r2", today=D)
    assert len(s.facts("asha")) == 1
