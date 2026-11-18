from datetime import datetime

from gem_agent.agents.schemas import ResolvedWindow
from gem_agent.index.db import BidIndex
from gem_agent.index.probe import probe_site
from gem_agent.index.sync import embed_pending
from gem_agent.tools.gem.models import Bid
from tests.agents.helpers import LOS
from tests.index.helpers import HashEmbedder, row


def at(day, hour=12):
    return datetime(2026, 11, day, hour)


def test_probe_counts_window_and_title_matches():
    index = BidIndex(":memory:")
    titles = ["Loan Origination System for RRBs", "LOST property lockers", "Digital lending platform with LOS",
              "Office chairs", "Old LOS bid"]
    published = [at(10), at(11), at(12), at(13), at(1)]
    index.upsert([Bid.from_listing(row(i + 1, p, at(28), t)) for i, (t, p) in enumerate(zip(titles, published))],
                 seen_at=at(15))
    embedder = HashEmbedder()
    embed_pending(index, embedder)
    window = ResolvedWindow(published_from=at(2, 0), published_to=at(16), closing_from=at(16))
    result = probe_site(index, embedder, LOS, window)
    assert result.window_count == 4
    assert result.strong == 2  # the old LOS bid is outside the window; LOST doesn't count
    assert result.top_titles[0] in titles[:3:2]
    index.close()
