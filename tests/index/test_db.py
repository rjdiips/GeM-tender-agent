from datetime import datetime, timedelta

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


def test_file_index_survives_reopen(tmp_path):
    path = tmp_path / "sub" / "bids.sqlite"
    first = BidIndex(path)
    first.upsert([bid(1)], seen_at=NOV(16))
    first.close()
    second = BidIndex(path)
    assert second.get(1).b_id == 1
    second.close()


# --- bid cards: what a vague-title bid buys, written from its bid document --------------------------------------


def test_card_joins_the_stored_text_and_survives_a_relisting(index):
    index.upsert([bid(1, title="Hiring of Agency for IT Projects- Milestone basis")], seen_at=NOV(16))
    index.set_card(1, "MSME Digital Lending Suite for a public sector bank; digital lending; loan origination")
    assert index.card(1).startswith("MSME Digital Lending Suite")
    text = index._conn.execute("SELECT embed_text FROM bids WHERE b_id = 1").fetchone()[0]
    assert "MSME Digital Lending Suite" in text and text.startswith("Hiring of Agency")
    index.upsert([bid(1, title="Hiring of Agency for IT Projects- Milestone basis")], seen_at=NOV(17))
    assert index.card(1) is not None


def test_bids_needing_a_card_are_open_vague_titles_without_one(index):
    index.upsert([bid(1, title="Hiring of Agency for IT Projects- Milestone basis"),
                  bid(2, title="Custom Bid for Services - ----"),
                  bid(3, title="Supply of office chairs")], seen_at=NOV(14))
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
    for b_id in (1, 2):
        index._conn.execute("INSERT INTO embeddings VALUES (?, 'm', 't', 4, x'00')", (b_id,))
    assert index.prune(closed_before=now - timedelta(days=30)) == 1
    assert index.get(1) is None and index.get(2) is not None
    assert index._conn.execute("SELECT b_id FROM embeddings").fetchall() == [(2,)]


def test_buyer_names_from_listings_are_kept_once(index):
    index.add_buyers([bid(1, department="RAILWAY BOARD"), bid(2, department="RAILWAY BOARD", ministry=None)])
    index.add_buyers([bid(3, department="BANKING SECTOR")])
    assert index.buyer_names("department") == ["BANKING SECTOR", "RAILWAY BOARD"]
    assert index.buyer_names("ministry") == ["Ministry of Finance"]
    assert index.get(1) is None  # names only: the bids themselves are not stored


def test_prune_keeps_the_bids_it_is_told_to(index):
    old = dict(published=NOV(1) - timedelta(days=40), closing=NOV(1) - timedelta(days=31))
    index.upsert([bid(1, **old), bid(2, **old)], seen_at=NOV(1))
    assert index.prune(closed_before=NOV(1) - timedelta(days=30), keep={2}) == 1
    assert index.get(1) is None and index.get(2) is not None
