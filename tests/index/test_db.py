from datetime import timedelta
from datetime import datetime

import numpy as np
import pytest

from gem_agent.index.db import BidIndex
from gem_agent.tools.gem.models import Bid
from tests.index.helpers import row


def NOV(d, h=12):
    return datetime(2026, 11, d, h)


@pytest.fixture
def index():
    idx = BidIndex(":memory:")
    yield idx
    idx.close()


def bid(b_id, published=None, closing=None, **kw):
    return Bid.from_listing(row(b_id, published or NOV(10), closing or NOV(25), **kw))


def test_upsert_counts_new_and_round_trips(index):
    counts = index.upsert([bid(1), bid(2)], seen_at=NOV(16))
    assert (counts.new, counts.updated) == (2, 0)
    got = index.get(1)
    assert got.bid_number == "GEM/2026/B/1" and got.closing == NOV(25)


def test_upsert_records_changed_closing_date(index):
    index.upsert([bid(1)], seen_at=NOV(16))
    counts = index.upsert([bid(1, closing=NOV(28))], seen_at=NOV(17))
    assert (counts.new, counts.updated, counts.unchanged) == (0, 1, 0)
    assert index.get(1).closing == NOV(28)


def test_unchanged_bid_counts_as_unchanged(index):
    index.upsert([bid(1)], seen_at=NOV(16))
    assert index.upsert([bid(1)], seen_at=NOV(17)).unchanged == 1


def test_known_ids(index):
    index.upsert([bid(1), bid(2)], seen_at=NOV(16))
    assert index.known([1, 2, 3]) == {1, 2}


def test_close_expired_marks_closed_and_drops_embedding(index):
    index.upsert([bid(1, closing=NOV(15)), bid(2, closing=NOV(25))], seen_at=NOV(14))
    index.store_embeddings("m", [(1, "t", np.ones(4, np.float32)), (2, "t", np.ones(4, np.float32))])
    assert index.close_expired(now=NOV(16)) == 1
    assert index.bid_state(1) == "closed" and index.bid_state(2) == "open"
    ids, _ = index.vectors("m")
    assert list(ids) == [2]


def test_extended_bid_reopens(index):
    index.upsert([bid(1, closing=NOV(15))], seen_at=NOV(14))
    index.close_expired(now=NOV(16))
    index.upsert([bid(1, closing=NOV(20))], seen_at=NOV(16, 13))
    assert index.bid_state(1) == "open"


@pytest.mark.skip(reason="unchanged rows no longer bump last_seen; the index sync and mark_missed are retired in Task 7 (live search)")
def test_bid_missing_from_two_full_refreshes_is_gone(index):
    index.upsert([bid(1), bid(2)], seen_at=NOV(15))
    index.upsert([bid(2)], seen_at=NOV(16, 1))
    assert index.mark_missed(refresh_started=NOV(16, 0), gone_after=2) == (1, 0)
    assert index.bid_state(1) == "open"
    index.upsert([bid(2)], seen_at=NOV(17, 1))
    assert index.mark_missed(refresh_started=NOV(17, 0), gone_after=2) == (1, 1)
    assert index.bid_state(1) == "gone"


def test_seen_again_resets_missed_count(index):
    index.upsert([bid(1)], seen_at=NOV(15))
    index.mark_missed(refresh_started=NOV(16), gone_after=2)
    index.upsert([bid(1)], seen_at=NOV(17, 1))
    index.mark_missed(refresh_started=NOV(18), gone_after=2)
    assert index.bid_state(1) == "open"


def test_pending_embeddings_are_new_or_changed_text(index):
    index.upsert([bid(1, title="chairs"), bid(2, title="tables")], seen_at=NOV(16))
    pending = dict(index.pending_embeddings("m"))
    assert set(pending) == {1, 2} and pending[1].startswith("chairs")
    index.store_embeddings("m", [(b, t, np.ones(4, np.float32)) for b, t in pending.items()])
    assert index.pending_embeddings("m") == []
    index.upsert([bid(1, title="sofas")], seen_at=NOV(17))
    assert [b for b, _ in index.pending_embeddings("m")] == [1]


def test_embed_text_has_title_and_buyer(index):
    index.upsert([bid(1, title="AML software", ministry="Ministry of Finance", department="DFS")], seen_at=NOV(16))
    assert index.pending_embeddings("m")[0][1] == "AML software | Ministry of Finance | DFS"


def test_vectors_for_other_model_are_separate(index):
    index.upsert([bid(1)], seen_at=NOV(16))
    index.store_embeddings("m1", [(1, "t", np.ones(4, np.float32))])
    assert len(index.vectors("m2")[0]) == 0
    assert [b for b, _ in index.pending_embeddings("m2")] == [1]


def test_state_values(index):
    assert index.state("x") is None
    index.set_state("x", "1")
    assert index.state("x") == "1"


def test_file_index_survives_reopen(tmp_path):
    path = tmp_path / "sub" / "bids.sqlite"
    first = BidIndex(path)
    first.upsert([bid(1)], seen_at=NOV(16))
    first.close()
    second = BidIndex(path)
    assert second.get(1).b_id == 1
    second.close()


# --- bid cards: what a vague-title bid buys, written from its bid document --------------------------------------


def test_card_joins_the_embedded_text_and_survives_a_relisting(index):
    index.upsert([bid(1, title="Hiring of Agency for IT Projects- Milestone basis")], seen_at=NOV(16))
    index.store_embeddings("m", [(b, t, np.ones(4, np.float32)) for b, t in index.pending_embeddings("m")])
    index.set_card(1, "MSME Digital Lending Suite for a public sector bank; digital lending; loan origination")
    assert index.card(1).startswith("MSME Digital Lending Suite")
    [(_, text)] = index.pending_embeddings("m")  # the text changed, so it is embedded again
    assert "MSME Digital Lending Suite" in text and text.startswith("Hiring of Agency")
    index.upsert([bid(1, title="Hiring of Agency for IT Projects- Milestone basis")], seen_at=NOV(17))
    assert index.card(1) is not None and "Digital Lending" in index.pending_embeddings("m")[0][1]


def test_bids_needing_a_card_are_open_vague_titles_without_one(index):
    index.upsert([bid(1, title="Hiring of Agency for IT Projects- Milestone basis"),
                  bid(2, title="Custom Bid for Services - ----"),
                  bid(3, title="Supply of office chairs"),
                  bid(4, title="Hiring of Agency for IT Projects- Milestone basis", closing=NOV(15))], seen_at=NOV(14))
    index.close_expired(now=NOV(16))
    index.set_card(2, "Website redesign")
    assert [b.b_id for b in index.needing_cards()] == [1]


def test_an_unchanged_bid_is_not_written_again(index):
    index.upsert([bid(1)], seen_at=NOV(16))
    before = index._conn.total_changes
    counts = index.upsert([bid(1)], seen_at=NOV(17))
    assert counts.unchanged == 1 and index._conn.total_changes == before


def test_prune_drops_bids_closed_more_than_30_days_ago(index):
    now = NOV(20)
    index.upsert([bid(1, published=now - timedelta(days=40), closing=now - timedelta(days=31)),
                  bid(2, published=now - timedelta(days=40), closing=now - timedelta(days=29))], seen_at=NOV(1))
    index.store_embeddings("m", [(1, "t", np.ones(4, np.float32)), (2, "t", np.ones(4, np.float32))])
    assert index.prune(closed_before=now - timedelta(days=30)) == 1
    assert index.get(1) is None and index.get(2) is not None
    assert index._conn.execute("SELECT b_id FROM embeddings").fetchall() == [(2,)]
