import pytest

from gem_agent.tools.pdf.ocr import ocr_page
from gem_agent.tools.pdf.placeholders import is_placeholder
from gem_agent.tools.pdf.text import PageText, extract_pdf
from tests.conftest import doc_bytes, fixture_bytes, make_pdf


def test_pages_with_text_layer_need_no_ocr():
    calls = []
    pages = extract_pdf(make_pdf("Scope of work for loan origination system", "Eligibility: average annual turnover of 10 crore"),
                        ocr=lambda pdf, i: calls.append(i) or "")
    assert [p.page for p in pages] == [1, 2]
    assert "loan origination" in pages[0].text and pages[1].method == "text"
    assert calls == []


def test_only_pages_without_text_get_ocr():
    calls = []
    pages = extract_pdf(make_pdf("Real text on this page of the tender document", ""),
                        ocr=lambda pdf, i: calls.append(i) or "scanned words")
    assert calls == [1]
    assert pages[1].method == "ocr" and pages[1].text == "scanned words"


def test_without_ocr_blank_pages_are_marked_none():
    pages = extract_pdf(make_pdf(""), ocr=None)
    assert pages[0].method == "none"


def test_unreadable_pdf_gives_no_pages():
    assert extract_pdf(b"not a pdf", ocr=None) == []


def test_real_bid_document_text():
    pages = extract_pdf(fixture_bytes("doc_bid.pdf"), ocr=None)
    assert len(pages) == 7 and "GEM/2026/B/8162911" in pages[0].text


@pytest.mark.slow
def test_tesseract_reads_scanned_page():
    text = ocr_page(doc_bytes("scanned_one_page.pdf"), 0)
    assert len(text.split()) > 30


def test_one_line_placeholders_are_recognised():
    for name in ("placeholder_refer_rfp.pdf", "placeholder_as_per_atc.pdf"):
        assert is_placeholder(extract_pdf(doc_bytes(name), ocr=None)), name


def test_real_documents_are_not_placeholders():
    assert not is_placeholder(extract_pdf(doc_bytes("rfp_firewall.pdf"), ocr=None))
    assert not is_placeholder(extract_pdf(fixture_bytes("doc_bid.pdf"), ocr=None))


def test_empty_or_scanned_without_text_is_not_a_placeholder():
    assert not is_placeholder([])
    assert not is_placeholder([PageText(page=1, text="", method="none")])
