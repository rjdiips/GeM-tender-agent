import json
from datetime import datetime, timedelta

from gem_agent.app.factory import labelled_b_ids, startup_prune
from gem_agent.index.db import BidIndex
from gem_agent.tools.gem.models import Bid
from tests.index.helpers import row

NOW = datetime(2026, 12, 30)


def test_labelled_bids_are_read_without_validating_the_set(tmp_path):
    path = tmp_path / "labelled_set.json"
    # a bucket value this branch's loader does not know: the ids are still read
    path.write_text(json.dumps({"topics": {}, "items": [{"b_id": 7, "bucket": "new-kind"}, {"b_id": 9}]}))
    assert labelled_b_ids(path) == {7, 9}
    assert labelled_b_ids(tmp_path / "missing.json") == set()


def test_the_startup_prune_keeps_the_labelled_bids(tmp_path):
    index = BidIndex(":memory:")
    closed = NOW - timedelta(days=40)
    index.upsert([Bid.from_listing(row(i, closed - timedelta(days=10), closed)) for i in (1, 2)], seen_at=closed)
    path = tmp_path / "labelled_set.json"
    path.write_text(json.dumps({"items": [{"b_id": 2}]}))
    assert startup_prune(index, path, NOW) == 1
    assert index.get(1) is None and index.get(2) is not None
    index.close()
