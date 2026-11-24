from gem_agent.graph.store import Outcome, Ruling, RunRecord
from gem_agent.graph.views import bid_result, bid_state, run_view, status_view
from tests.agents.helpers import LOS
from tests.graph.fakes import NOW, FakeJudge, bid

NOT_READ = "Not a match: buys Lockers. Ruled out by the description check and the title check."


def make(*, relevant=False, borderline=False, ruling=None, restored=False, error="", verdict=True,
         not_read="", read_because=None):
    v = FakeJudge()._verdict(LOS, bid(1), relevant, "judge", borderline) if verdict else None
    return Outcome(b_id=1, group="g", bid=bid(1), verdict=v, ruling=ruling, restored=restored, error=error,
                   not_read=not_read, read_because=read_because)


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


def test_a_bid_not_read_is_quarantined_with_its_reason():
    o = Outcome(b_id=1, group="LOS", bid=bid(1), not_read=NOT_READ)
    r = bid_result("r", o)
    assert r["state"] == "quarantined"
    assert r["reason"].startswith("Not a match: buys Lockers")
    assert r["read_because"] is None


def test_restoring_a_bid_not_read_makes_it_relevant_and_keeps_the_reason():
    o = Outcome(b_id=1, group="LOS", bid=bid(1), not_read=NOT_READ)
    r = bid_result("r", o.model_copy(update={"restored": True}))
    assert r["state"] == "relevant"
    assert r["reason"].startswith("Not a match: buys Lockers")


def test_a_read_bid_says_why_it_was_read():
    r = bid_result("r", make(relevant=True, read_because="net"))
    assert r["read_because"] == "net"
    assert r["reason"] == "RFP asks for an LOS."


def test_a_not_ruling_reason_precedes_the_not_read_reason():
    r = bid_result("r", make(ruling=ruling("not"), not_read=NOT_READ, error="GeM returned 503"))
    assert r["reason"] == "Your ruling: Loan systems for banks only"


def test_the_not_read_reason_precedes_the_verdict_and_error():
    r = bid_result("r", make(not_read=NOT_READ, error="GeM returned 503"))
    assert r["reason"] == NOT_READ


def test_a_counts_ruling_falls_through_to_the_not_read_reason():
    r = bid_result("r", make(ruling=ruling("counts"), not_read=NOT_READ))
    assert r["state"] == "relevant"
    assert r["reason"] == NOT_READ


def test_error_text_is_the_last_reason():
    r = bid_result("r", make(verdict=False, error="GeM returned HTTP error: 503"))
    assert r["reason"] == (
        "Not read: GeM had an error (503) when its documents were fetched. Searching again later may work."
    )
    assert r["read_because"] is None


def test_not_read_bids_are_results_in_run_and_status_views():
    record = RunRecord(run_id="r", user="asha", title="LOS", request_text="LOS tenders", created=NOW)
    outcomes = {
        1: Outcome(b_id=1, group="LOS", bid=bid(1), not_read=NOT_READ),
        2: Outcome(b_id=2, group="LOS", bid=bid(2)),
    }
    view = run_view(record, outcomes, [])
    assert [r["b_id"] for r in view["results"]] == [1]
    assert view["results"][0]["reason"] == NOT_READ
    assert status_view(record, outcomes, [])["results"] == 1
