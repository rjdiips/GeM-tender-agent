from datetime import datetime

from gem_agent.agents.schemas import DateWindow

NOW = datetime(2026, 11, 16, 13, 40)


def test_published_within_two_weeks():
    w = DateWindow(kind="published", last_days=14).resolve(NOW)
    assert (w.published_from, w.published_to) == (datetime(2026, 11, 2), NOW)
    assert w.closing_from == NOW  # still open


def test_closing_within_ten_days():
    w = DateWindow(kind="closing", next_days=10).resolve(NOW)
    assert (w.closing_from, w.closing_to) == (NOW, datetime(2026, 11, 26, 23, 59, 59))
    assert w.published_from is None


def test_explicit_dates():
    w = DateWindow(kind="published", from_date="2026-11-05", to_date="2026-11-10").resolve(NOW)
    assert (w.published_from, w.published_to) == (datetime(2026, 11, 5), datetime(2026, 11, 10, 23, 59, 59))


def test_no_window_means_all_open_bids():
    w = DateWindow().resolve(NOW)
    assert w.published_from is None and w.closing_from == NOW


def test_describe_for_progress_line():
    assert DateWindow(kind="published", last_days=14).resolve(NOW).describe() == "published 2–16 Nov"
    assert DateWindow(kind="closing", next_days=10).resolve(NOW).describe() == "closing 16–26 Nov"


def test_kind_is_inferred_when_the_model_leaves_it_open():
    assert DateWindow(kind="open", last_days=14).resolve(NOW).published_from == datetime(2026, 11, 2)
    w = DateWindow(kind="open", from_date="2026-11-16", to_date="2026-11-26").resolve(NOW)
    assert w.closing_to == datetime(2026, 11, 26, 23, 59, 59)


def test_model_facing_window_schema_requires_every_field():
    from gem_agent.agents.chat import WindowOut

    assert set(WindowOut.model_json_schema()["required"]) == {"kind", "last_days", "next_days", "from_date", "to_date"}


def test_relative_numbers_win_over_conflicting_dates():
    w = DateWindow(kind="closing", next_days=10, from_date="2026-11-16", to_date="2026-11-16").resolve(NOW)
    assert w.closing_to == datetime(2026, 11, 26, 23, 59, 59)
    w = DateWindow(kind="published", last_days=14, from_date="2026-11-15").resolve(NOW)
    assert w.published_from == datetime(2026, 11, 2)
