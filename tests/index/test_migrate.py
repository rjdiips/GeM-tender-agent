from datetime import datetime

from gem_agent.index.db import BidIndex
from gem_agent.index.migrate import migrate_bids
from gem_agent.tools.gem.models import Bid
from tests.index.helpers import row


def _bid(b_id):
    return Bid.from_listing(row(b_id, datetime(2026, 11, 10), datetime(2026, 11, 25)))


def test_migrate_copies_cards_tried_markers_and_labelled_bids_only(tmp_path):
    old, new = BidIndex(tmp_path / "old.sqlite"), BidIndex(tmp_path / "new.sqlite")
    old.upsert([_bid(i) for i in (1, 2, 3, 4)], seen_at=datetime(2026, 11, 16))
    old.set_card(1, "AML suite; AML")
    old.set_card(2, "")
    assert migrate_bids(old, new, {4}) == 3
    assert new.card(1) == "AML suite; AML" and new.card(2) == "" and new.card(4) is None
    assert new.get(3) is None and new.get(4) is not None
    assert new.get(1).raw == old.get(1).raw
