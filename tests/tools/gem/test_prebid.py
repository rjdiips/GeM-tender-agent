from datetime import datetime

from gem_agent.tools.gem.prebid import parse_pre_bid

# GeM's bid PDF, as extracted (data/cache, 17 Nov): the Hindi labels come out garbled, the date follows the venue label
GEM_BLOCK = ("Pre Bid Detail(s)\nमूXय िभ*नता खंड द'तावेज़/Pre-Bid\nDate and Time\n9ी-\x01बड 'थान/Pre-Bid Venue\n"
             "25-08-2026 15:00:00\nTender Office\nDigital Radiography System (V2) ( 1 pieces ) \n")


def test_reads_the_pre_bid_date_from_gems_bid_document():
    found = parse_pre_bid(GEM_BLOCK)
    assert found is not None
    when, line = found
    assert when == datetime(2026, 8, 25, 15, 0)
    assert "25-08-2026 15:00:00" in line


def test_no_pre_bid_section_means_none():
    assert parse_pre_bid("Bid End Date/Time 07-12-2026 16:00:00\nBid Opening Date/Time 07-12-2026 16:30:00") is None


def test_a_date_far_after_the_heading_is_not_taken():
    assert parse_pre_bid("Pre Bid Detail(s)\n" + "x " * 400 + "25-08-2026 15:00:00") is None
