from gem_agent.graph.store import Outcome, Ruling
from gem_agent.graph.views import bid_result, bid_state
from tests.agents.helpers import LOS
from tests.graph.fakes import NOW, FakeJudge, bid


def make(*, relevant=False, borderline=False, ruling=None, restored=False, error="", verdict=True):
    v = FakeJudge()._verdict(LOS, bid(1), relevant, "judge", borderline) if verdict else None
    return Outcome(b_id=1, group="g", bid=bid(1), verdict=v, ruling=ruling, restored=restored, error=error)


def ruling(decision):
    return Ruling(decision=decision, by="you", fact_id="f1", fact="Loan systems for banks only", at=NOW)


def test_bid_state_rules():
    assert bid_state(make(relevant=True)) == "relevant"
    assert bid_state(make()) == "quarantined"
    assert bid_state(make(verdict=False, error="boom")) == "quarantined"
    assert bid_state(make(borderline=True)) == "borderline"
    assert bid_state(make(borderline=True, ruling=ruling("counts"))) == "relevant"
    assert bid_state(make(borderline=True, ruling=ruling("not"))) == "quarantined"
    assert bid_state(make(relevant=True, ruling=ruling("not"))) == "quarantined"
    assert bid_state(make(borderline=True, ruling=ruling("not"), restored=True)) == "relevant"
    assert bid_state(make(restored=True)) == "relevant"


def test_bid_result_carries_state_question_and_ruling():
    r = bid_result("r", make(borderline=True))
    assert (r["state"], r["relevant"], r["ruling"]) == ("borderline", False, None)
    assert r["question"] == "Does bid 1 count for you?"
    r = bid_result("r", make(borderline=True, ruling=ruling("not")))
    assert r["relevant"] is False and r["reason"] == "Your ruling: Loan systems for banks only"
    assert r["ruling"] == {"decision": "not", "by": "you", "fact": "Loan systems for banks only"}
    assert bid_result("r", make(borderline=True, ruling=ruling("counts")))["relevant"] is True
