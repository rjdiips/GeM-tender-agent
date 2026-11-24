from datetime import date, datetime, time

from gem_agent.agents.schemas import DateWindow

NOW = datetime(2026, 11, 16, 13, 40)
LATER = datetime(2026, 11, 23, 13, 0)


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


def test_closing_after_a_named_date_is_described_as_such():
    w = DateWindow(kind="closing", from_date=date(2026, 12, 1)).resolve(NOW, explicit_dates=True)
    assert w.closing_to is None and w.describe() == "closing after 1 Dec"


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


def test_explicit_dates_win_over_last_days():
    w = DateWindow(kind="published", last_days=30, from_date=date(2026, 10, 1), to_date=date(2026, 10, 31))
    r = w.resolve(NOW, explicit_dates=True)
    assert r.published_from == datetime(2026, 10, 1) and r.published_to == datetime(2026, 10, 31, 23, 59, 59)


def test_last_days_win_when_request_names_no_dates():
    w = DateWindow(kind="published", last_days=14, from_date=date(2026, 10, 1))
    assert w.resolve(LATER).published_from == datetime(2026, 11, 9)


def test_last_days_zero_is_today():
    assert DateWindow(kind="published", last_days=0).resolve(LATER).published_from == datetime(2026, 11, 23)


def test_open_kind_with_zero_last_days_is_today():
    r = DateWindow(kind="open", last_days=0).resolve(LATER)
    assert r.published_from == datetime.combine(LATER.date(), time())  # not the 14-day default
    assert r.published_to == LATER
    assert DateWindow(kind="open", last_days=14).resolve(LATER).published_from == datetime(2026, 11, 9)


def test_to_date_only_published_window_keeps_the_named_end_and_names_it_in_the_card():
    w = DateWindow(kind="published", last_days=14, to_date=date(2026, 10, 31))
    r = w.resolve(NOW, explicit_dates=True)
    assert (r.published_from, r.published_to) == (None, datetime(2026, 10, 31, 23, 59, 59))
    assert r.describe() == "published until 31 Oct"  # not "open now": the user named an end date


def test_closing_after_a_date_stays_open_ended():
    r = DateWindow(kind="closing", from_date=date(2026, 12, 1)).resolve(NOW, explicit_dates=True)
    assert r.closing_from == datetime(2026, 12, 1) and r.closing_to is None


def test_closing_dates_the_user_named_win_over_model_next_days():
    w = DateWindow(kind="closing", next_days=10, from_date=date(2026, 12, 1), to_date=date(2026, 12, 10))
    r = w.resolve(NOW, explicit_dates=True)
    assert (r.closing_from, r.closing_to) == (datetime(2026, 12, 1), datetime(2026, 12, 10, 23, 59, 59))
