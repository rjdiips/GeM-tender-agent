import logging
from datetime import datetime

import pytest

from gem_agent.cli import main, render_event, setup_logging
from gem_agent.config import Settings
from gem_agent.eval.labelled import Label, LabelledSet
from gem_agent.index.db import BidIndex
from gem_agent.tools.gem.models import Bid
from tests.index.helpers import row


def test_progress_and_results_print_as_lines():
    assert render_event({"type": "progress", "event": {"at": "2026-11-16T13:40:09", "text": "Shortlist: 9 bids."}}) == \
        "13:40:09  Shortlist: 9 bids."
    line = render_event({"type": "result", "bid": {"bid_number": "GEM/2026/B/1", "title": "LOS for RRBs", "relevant": True,
                                                    "closing": "2026-11-20T15:00:00", "reason": "Asks for an LOS.",
                                                    "confidence": 0.9, "group": "Loan Origination System"}})
    assert line.startswith("  ✔ GEM/2026/B/1") and "closes 20 Nov 15:00" in line and "Asks for an LOS." in line


def test_quarantined_and_messages():
    assert render_event({"type": "result", "bid": {"bid_number": "GEM/2026/B/2", "title": "Lockers", "relevant": False,
                                                    "closing": "2026-11-20T15:00:00", "reason": "Lockers.", "confidence": 0.9,
                                                    "group": "x"}}).startswith("  ✘ GEM/2026/B/2")
    assert render_event({"type": "message", "text": "Hello"}) == "Hello"
    assert render_event({"type": "started", "run": {"run_id": "abc"}}) == "run abc"


def test_bids_migrate_copies_cards_from_the_old_index(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path / "data")
    monkeypatch.setattr("gem_agent.cli.get_settings", lambda: settings)
    old = BidIndex(tmp_path / "old.sqlite")
    old.upsert([Bid.from_listing(row(i, datetime(2026, 11, 10), datetime(2026, 11, 25))) for i in (1, 2)],
               seen_at=datetime(2026, 11, 16))
    old.set_card(1, "AML suite; AML")
    old.close()
    labelled = tmp_path / "labelled.json"
    LabelledSet(topics={}, items=[Label(topic="t", b_id=2, bid_number="GEM/2026/B/2", bucket="random",
                                        relevant=False)]).save(labelled)
    assert main(["bids", "migrate", "--from", str(tmp_path / "old.sqlite"), "--labelled", str(labelled)]) == 0
    new = BidIndex(settings.bid_cache_path)
    assert new.card(1) == "AML suite; AML"
    assert new.get(2) is not None  # a labelled bid comes across without a card


def test_the_index_command_is_gone():
    with pytest.raises(SystemExit):
        main(["index", "sync"])


def test_httpx_logs_at_warning_after_setup():
    setup_logging(quiet=False)
    assert logging.getLogger("httpx").level == logging.WARNING
