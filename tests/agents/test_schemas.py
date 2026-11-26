from gem_agent.agents.schemas import ResolvedWindow


def test_describe_closing_flag_without_date_does_not_crash():
    w = ResolvedWindow(closing_until=True, closing_to=None)
    assert w.describe() == "open now"      # falls through, no AttributeError
    w2 = ResolvedWindow(closing_after=True, closing_from=None)
    assert w2.describe() == "open now"
