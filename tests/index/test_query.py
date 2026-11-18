from datetime import datetime

import pytest

from gem_agent.index.db import BidIndex
from gem_agent.index.query import IndexQuery, search_index
from gem_agent.index.sync import embed_pending
from gem_agent.tools.gem.models import Bid
from tests.index.helpers import FixedReranker, HashEmbedder, row


def at(day, hour=12):
    return datetime(2026, 11, day, hour)


TITLES = {
    1: ("Loan Origination System software for regional rural banks", at(10), at(20)),
    2: ("Supply of office chairs", at(11), at(18)),
    3: ("LOST property lockers", at(12), at(19)),
    4: ("Anti money laundering software", at(1), at(21)),
    5: ("Digital lending platform with loan origination", at(14), at(25)),
}


@pytest.fixture
def index():
    idx = BidIndex(":memory:")
    idx.upsert([Bid.from_listing(row(i, p, c, t)) for i, (t, p, c) in TITLES.items()], seen_at=at(15))
    idx.upsert([Bid.from_listing(row(6, at(13), at(17), "Hiring of agency", ministry="Ministry of Defence",
                                     bid_type=2))], seen_at=at(15))
    yield idx
    idx.close()


@pytest.fixture
def embedder(index):
    e = HashEmbedder()
    embed_pending(index, e)
    return e


def ids(hits):
    return [h.bid.b_id for h in hits]


def test_published_window_filter(index, embedder):
    hits = search_index(index, IndexQuery(published_from=at(10), published_to=at(13)), embedder)
    assert set(ids(hits)) == {1, 2, 3, 6}


def test_without_text_closing_soonest_first(index, embedder):
    assert ids(search_index(index, IndexQuery(), embedder))[:3] == [6, 2, 3]


def test_semantic_and_word_match_rank_topic_first(index, embedder):
    q = IndexQuery(text="loan origination system for banks", terms=["LOS", "loan origination"])
    hits = search_index(index, q, embedder)
    assert set(ids(hits)[:2]) == {1, 5}


def test_acronym_does_not_match_lookalike_word(index, embedder):
    hits = search_index(index, IndexQuery(terms=["LOS"]), embedder)
    assert 3 not in ids(hits)


def test_reranker_orders_the_top(index, embedder):
    q = IndexQuery(text="loan origination", terms=["loan origination"])
    hits = search_index(index, q, embedder, reranker=FixedReranker("lending"))
    assert ids(hits)[0] == 5 and hits[0].rerank == 1.0


def test_rerank_limited_to_top_n(index, embedder):
    reranker = FixedReranker("x")
    search_index(index, IndexQuery(text="software", rerank_top=2), embedder, reranker=reranker)
    assert reranker.seen == [2]


def test_buyer_and_type_filters(index, embedder):
    assert ids(search_index(index, IndexQuery(ministries=["Ministry of Defence"]), embedder)) == [6]
    assert 6 not in ids(search_index(index, IndexQuery(bid_types=[1]), embedder))


def test_closed_bids_are_excluded(index, embedder):
    index.close_expired(now=at(18, 13))
    assert not {2, 6} & set(ids(search_index(index, IndexQuery(), embedder)))


def test_limit(index, embedder):
    assert len(search_index(index, IndexQuery(limit=2), embedder)) == 2
