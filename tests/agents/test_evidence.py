from gem_agent.agents.evidence import verify_quote

PAGE = """Scope of Work
The bidder shall supply, implement and maintain a Loan Origination
System (LOS) for all 1,200 branches of the bank."""


def test_exact_quote_is_verified():
    assert verify_quote("supply, implement and maintain a Loan Origination System (LOS)", PAGE)


def test_quote_matches_across_line_breaks_and_case():
    assert verify_quote("MAINTAIN A LOAN ORIGINATION SYSTEM (LOS) FOR ALL 1,200 BRANCHES", PAGE)


def test_small_differences_still_verify():
    assert verify_quote("The bidder shall supply, implement and maintain the Loan Origination System", PAGE)


def test_invented_quote_fails():
    assert not verify_quote("The bank requires an anti money laundering solution", PAGE)


def test_tiny_quote_is_not_evidence():
    assert not verify_quote("LOS", PAGE)


GEM_PAGE = ("कुल मा ा/Total Quantity\n29\nव'तु (ेणी /Item Category\nRevolving Chair (V5) (Q2)\n"
            "एमएसएमई के िलए अनुभव/Years of Past Experience Required\n1 Year (s)")


def test_a_quote_stitched_with_an_ellipsis_is_checked_part_by_part_in_any_order():
    # 8251705 (17 Nov): two real fields, cited in the opposite order to the page, were shown "not found"
    assert verify_quote("Item Category: Revolving Chair (V5) (Q2) ... Total Quantity: 29", GEM_PAGE)
    assert verify_quote("Item Category: Revolving Chair (V5) (Q2) … Total Quantity 29", GEM_PAGE)


def test_every_stitched_part_must_be_there():
    assert not verify_quote("Item Category: Revolving Chair (V5) (Q2) ... Total Quantity: 30", GEM_PAGE)
    assert not verify_quote("Item Category: Office Table (V2) ... Total Quantity: 29", GEM_PAGE)


def test_a_three_word_quote_is_too_short_even_when_present():
    from gem_agent.agents.evidence import too_short
    assert too_short("Total Quantity 29") and not verify_quote("Total Quantity 29", GEM_PAGE)
    assert not too_short("Item Category: Revolving Chair")


def test_a_spaced_slash_joins_parts_like_an_ellipsis():
    assert verify_quote("Item Category: Revolving Chair (V5) (Q2) / Total Quantity: 29", GEM_PAGE)
    assert verify_quote("Ministry of Finance / Department of Financial Services Canara Bank",
                        "Ministry of Finance / Department of Financial Services Canara Bank")
