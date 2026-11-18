from gem_agent.cli import render_event


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
