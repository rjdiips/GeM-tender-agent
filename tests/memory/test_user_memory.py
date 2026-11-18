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


def test_a_ruling_is_active_at_once_and_tied_to_its_topic(tmp_path):
    m = MemoryStore(tmp_path)
    f = m.add_ruling("asha", "AI that is one part of a larger system counts as an AI platform",
                     topic="AI platform", run_id="r1", today=D, evidence="you ruled on GEM/2026/B/7914442 on 18 Nov")
    assert f.active and f.kind == "ruling" and f.source == "stated"
    assert m.ruling_facts("asha", "AI platform") == [f]
    assert m.ruling_facts("asha", "Anti-Money Laundering") == []
    assert m.ruling_facts("ravi", "AI platform") == []


def test_rulings_stay_out_of_the_chat_prompt_and_its_limit(tmp_path):
    m = MemoryStore(tmp_path)
    m.add_stated("asha", "We sell LOS software", run_id="r0", today=D)
    for i in range(25):
        m.add_ruling("asha", f"ruling {i}", topic="AI platform", run_id="r1", today=D, evidence="e")
    assert [f.fact for f in m.prompt_facts("asha")] == ["We sell LOS software"]


def test_forget_with_evidence_records_why(tmp_path):
    m = MemoryStore(tmp_path)
    f = m.add_ruling("asha", "ruling", topic="AI platform", run_id="r1", today=D, evidence="old")
    m.forget("asha", f.id, "replaced by your ruling on GEM/2026/B/1 on 18 Nov")
    [g] = m.facts("asha")
    assert g.removed and not g.active and g.evidence == "replaced by your ruling on GEM/2026/B/1 on 18 Nov"
    assert m.ruling_facts("asha", "AI platform") == []


def test_a_general_fact_with_the_same_words_does_not_merge_with_a_ruling(tmp_path):
    m = MemoryStore(tmp_path)
    r = m.add_ruling("asha", "We sell LOS software", topic="LOS", run_id="r1", today=D, evidence="e")
    g = m.add_stated("asha", "We sell LOS software", run_id="r2", today=D)
    assert g.id != r.id and g.kind == "general" and r.kind == "ruling"
    m.apply("asha", [MemoryChange(action="add", fact="We sell LOS software", fact_id=None, evidence="x")], run_id="r3", today=D)
    assert [f.kind for f in m.facts("asha")] == ["ruling", "general"]
    assert m.facts("asha")[0].runs == ["r1"]


def test_apply_cannot_change_a_ruling_by_its_id(tmp_path):
    m = MemoryStore(tmp_path)
    r = m.add_ruling("asha", "ruling", topic="AI platform", run_id="r1", today=D, evidence="e")
    before = m.facts("asha")
    for action in ("support", "weaken", "remove", "update"):
        m.apply("asha", [MemoryChange(action=action, fact="changed", fact_id=r.id, evidence="x")], run_id="r2", today=D)
    assert m.facts("asha") == before
    m.forget("asha", r.id)  # the user still can
    assert m.ruling_facts("asha", "AI platform") == []
