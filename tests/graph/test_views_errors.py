from gem_agent.graph.views import error_text


def test_a_bid_that_could_not_be_fetched_says_why_in_words():
    raw = ('https://bidplus.gem.gov.in/public-bid-other-details/10120293: 403 (<!DOCTYPE html>\n<html lang="en">'
           '<head><title>Error</title>')
    assert error_text(raw) == ("Not read: GeM refused the request (403) when its documents were fetched. "
                               "Searching again later may work.")
    assert error_text("ReadTimeout: timed out").startswith("Not read: GeM did not answer in time")
