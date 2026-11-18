from gem_agent.tools.gem.bid_fields import buyer_org
from gem_agent.tools.pdf.text import extract_pdf
from tests.conftest import fixture_bytes


def test_organisation_from_a_real_bid_document():
    text = extract_pdf(fixture_bytes("doc_bid.pdf"), None)[0].text
    assert buyer_org(text) == "Union Bank Of India (ubi)"


def test_organisation_tolerates_the_hindi_label_lines():
    text = ("मं ालय/रा!य का नाम/Ministry/State Name\nMinistry Of Finance\n"
            "वभाग का नाम/Department Name\nDepartment Of Financial Services\n"
            "संगठन का नाम/Organisation Name\n\nNational Bank For Financing Infrastructure And Dev\n"
            "Buyer Email id: bijo@nabfid.org")
    assert buyer_org(text) == "National Bank For Financing Infrastructure And Dev"


def test_no_organisation_gives_none():
    assert buyer_org("Bid Details\nBid End Date 11-11-2026") is None
    assert buyer_org("Organisation Name\nBuyer Email id: x@y") is None
