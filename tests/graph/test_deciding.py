import threading
from datetime import datetime

import numpy as np
import pytest

from gem_agent.agents.schemas import TopicDefinition
from gem_agent.agents.searcher import Candidate
from gem_agent.graph.deciding import describe_order, not_read_reason, route, screen_kept, title_similarity
from gem_agent.index.db import Description
from gem_agent.tools.gem.models import Bid

NOW = datetime(2026, 11, 24)
DROPPED = "title screen: no"
OWN = "fullText|LOS"
CHECKS = ["the description check", "the title check"]


def candidate(b_id, *, closing=datetime(2026, 11, 20), why=("title words",)):
    bid = Bid(b_id=b_id, bid_number=f"GEM/2026/B/{b_id}", title=f"Bid {b_id}", closing=closing, bid_type=1)
    return Candidate(bid=bid, group="LOS", why=list(why))


def kept(b_id=1):
    return candidate(b_id)


def dropped(b_id=1):
    return candidate(b_id, why=(DROPPED, OWN))


def desc(*, subject="Lockers for branches", has_scope=True):
    return Description(b_id=1, doc_hash="hash", subject=subject, terms=["lockers"], sources=["bid.pdf"],
                       has_scope=has_scope, buyer_org="A bank", model="m", prompt_version="1", written_at=NOW)


def ids(cands):
    return [c.bid.b_id for c in cands]


@pytest.mark.parametrize(("why", "want"), [
    ((), True),
    (("title words",), True),
    ((OWN, DROPPED), True),
    ((DROPPED, OWN), False),
])
def test_screen_kept_is_false_only_when_the_first_reason_is_the_title_screen_no(why, want):
    assert screen_kept(candidate(1, why=why)) is want


@pytest.mark.parametrize(("is_kept", "has_scope", "call", "want"), [
    (True, True, "match", "match"),
    (True, True, "unsure", "unsure"),
    (True, True, "no", "net"),
    (True, True, None, "today"),
    (True, False, "match", "match"),
    (True, False, "unsure", "unsure"),
    (True, False, "no", "net"),
    (True, False, None, "today"),
    (False, True, "match", "match"),
    (False, True, "unsure", "unsure"),
    (False, True, "no", "drop"),
    (False, True, None, "unseen"),
    (False, False, "match", "match"),
    (False, False, "unsure", "unsure"),
    (False, False, "no", "drop"),
    (False, False, None, "unseen"),
])
def test_a_bid_is_never_dropped_on_one_no_while_the_net_is_on(is_kept, has_scope, call, want):
    c = kept() if is_kept else dropped()
    assert route(c, desc(has_scope=has_scope), call, safety_net=True) == want


@pytest.mark.parametrize(("is_kept", "has_scope", "call", "want"), [
    (True, True, "match", "match"),
    (True, True, "unsure", "unsure"),
    (True, True, "no", "drop"),
    (True, True, None, "today"),
    (True, False, "match", "match"),
    (True, False, "unsure", "unsure"),
    (True, False, "no", "net"),
    (True, False, None, "today"),
    (False, True, "match", "match"),
    (False, True, "unsure", "unsure"),
    (False, True, "no", "drop"),
    (False, True, None, "unseen"),
    (False, False, "match", "match"),
    (False, False, "unsure", "unsure"),
    (False, False, "no", "drop"),
    (False, False, None, "unseen"),
])
def test_with_the_net_off_a_no_with_scope_drops_and_one_without_takes_todays_path(is_kept, has_scope, call, want):
    c = kept() if is_kept else dropped()
    assert route(c, desc(has_scope=has_scope), call, safety_net=False) == want


@pytest.mark.parametrize("call", ["match", "unsure", "no", None])
@pytest.mark.parametrize("safety_net", [True, False])
@pytest.mark.parametrize(("is_kept", "want"), [(True, "today"), (False, "unseen")])
def test_an_empty_description_takes_todays_path(is_kept, want, safety_net, call):
    c = kept() if is_kept else dropped()
    assert route(c, desc(subject="", has_scope=True), call, safety_net=safety_net) == want


@pytest.mark.parametrize("call", ["match", "unsure", "no", None])
@pytest.mark.parametrize("safety_net", [True, False])
@pytest.mark.parametrize(("is_kept", "want"), [(True, "today"), (False, "unseen")])
def test_undescribed_bids_take_todays_path_even_with_the_net_off(is_kept, want, safety_net, call):
    c = kept() if is_kept else dropped()
    assert route(c, None, call, safety_net=safety_net) == want


def test_describe_order_own_search_then_kept_then_closing_then_similarity():
    cands = [
        candidate(4, closing=None, why=(OWN, DROPPED)),
        candidate(10, closing=datetime(2026, 11, 21)),
        candidate(3, closing=datetime(2026, 11, 20, 18), why=(OWN,)),
        candidate(12, closing=datetime(2026, 11, 19), why=(DROPPED,)),
        candidate(1, closing=datetime(2026, 11, 20, 12), why=(OWN,)),
        candidate(8, closing=None),
        candidate(5, closing=datetime(2026, 11, 25), why=(OWN,)),
        candidate(11, closing=None, why=(DROPPED,)),
        candidate(2, closing=datetime(2026, 11, 20, 9), why=(OWN,)),
        candidate(9, closing=datetime(2026, 11, 20)),
    ]
    similarity = {1: 0.9, 2: 0.2, 3: 0.2, 4: 1.0, 5: 0.0, 8: 1.0, 9: 0.0, 10: 1.0, 11: 0.0, 12: 1.0}

    assert ids(describe_order(cands, similarity)) == [1, 2, 3, 5, 4, 9, 10, 8, 12, 11]


def test_describe_order_keeps_bids_with_low_or_missing_similarity():
    cands = [candidate(2), candidate(1)]

    assert ids(describe_order(cands, {1: 0.0})) == [1, 2]


def test_not_read_reason_names_the_subject_and_the_checks():
    assert not_read_reason(desc(), CHECKS) == (
        "Not a match: buys Lockers for branches. Ruled out by the description check and the title check."
    )


@pytest.mark.parametrize("description", [None, desc(subject="")])
def test_not_read_reason_omits_an_unusable_description(description):
    assert not_read_reason(description, CHECKS) == (
        "Not a match. Ruled out by the description check and the title check."
    )


def test_not_read_reason_names_one_check_without_an_and():
    assert not_read_reason(desc(), ["the title check"]) == (
        "Not a match: buys Lockers for branches. Ruled out by the title check."
    )


class RecordingEmbedder:
    model_name = "recording"

    def __init__(self, vectors):
        self.vectors = np.asarray(vectors, dtype=np.float32)
        self.calls = []
        self.thread_ids = []

    def encode(self, texts):
        self.calls.append(texts)
        self.thread_ids.append(threading.get_ident())
        return self.vectors


async def test_title_similarity_encodes_the_topic_and_titles_off_the_event_loop_and_returns_cosines():
    topic = TopicDefinition(user_text="LOS tenders", meaning="Loan Origination System for banks",
                            match_terms=["LOS", "loan origination"])
    embedder = RecordingEmbedder([[3, 4], [4, 3], [0, 10]])
    loop_thread = threading.get_ident()

    scores = await title_similarity(embedder, topic, ["matching title", "vertical title"])

    assert scores == pytest.approx([0.96, 0.8])  # raw dot products would be 24 and 40
    assert embedder.calls == [["Loan Origination System for banks; LOS; loan origination",
                               "matching title", "vertical title"]]
    assert embedder.thread_ids[0] != loop_thread


async def test_title_similarity_returns_zero_scores_when_encoding_fails(caplog):
    class BrokenEmbedder:
        def encode(self, texts):
            raise RuntimeError("remote embedder unavailable")

    topic = TopicDefinition(user_text="LOS tenders", meaning="Loan Origination System")

    assert await title_similarity(BrokenEmbedder(), topic, ["one", "two"]) == [0.0, 0.0]
    assert caplog.text.count("title similarity unavailable") == 1


async def test_title_similarity_is_zero_for_every_title_without_an_embedder():
    topic = TopicDefinition(user_text="LOS tenders", meaning="Loan Origination System")

    assert await title_similarity(None, topic, ["one", "two"]) == [0.0, 0.0]
    assert await title_similarity(None, topic, []) == []
