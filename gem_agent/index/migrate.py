"""One-off copy of the old index's card work into the bid cache."""
from __future__ import annotations

from gem_agent.index.db import BidIndex
from gem_agent.tools.gem.models import site_now


def migrate_bids(old: BidIndex, new: BidIndex, keep_ids: set[int]) -> int:
    """Copy every bid with a card ('' included: it marks a bid already tried) plus every bid in keep_ids,
    with its raw listing and card. Returns the number copied."""
    cards = dict(old.labelled())
    copied = 0
    for b_id in sorted(cards.keys() | keep_ids):
        bid = old.get(b_id)
        if bid is None:
            continue
        new.upsert([bid], seen_at=site_now())
        if cards.get(b_id) is not None:
            new.set_card(b_id, cards[b_id])
        copied += 1
    return copied
