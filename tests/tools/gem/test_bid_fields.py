from gem_agent.tools.gem.bid_fields import buyer_org, department, ministry
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


BILINGUAL = ("मं ालय/रा!य का नाम/Ministry/State Name\nMinistry Of Finance\n"
             "\x01वभाग का नाम/Department Name\nDepartment Of Economic Affairs\n"
             "संगठन का नाम/Organisation Name\nSecurity Printing And Minting Corporation Of India Limited\n")


def test_ministry_and_department_from_the_bilingual_label_lines():
    assert ministry(BILINGUAL) == "Ministry Of Finance"
    assert department(BILINGUAL) == "Department Of Economic Affairs"


def test_ministry_and_department_from_a_real_bid_document():
    text = extract_pdf(fixture_bytes("doc_bid.pdf"), None)[0].text
    assert ministry(text) == "Ministry Of Finance"
    assert department(text) == "Department Of Financial Services"


def test_a_blank_ministry_or_department_gives_none():
    text = "Ministry/State Name\n\nवभाग का नाम/Department Name\n\nसंगठन का नाम/Organisation Name\nX\n"
    assert ministry(text) is None and department(text) is None
    assert ministry("Bid Details") is None and department("Bid Details") is None
