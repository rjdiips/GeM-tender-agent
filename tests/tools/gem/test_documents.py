import httpx
import pytest

from gem_agent.tools.gem.documents import (
    attachment_links,
    document_path,
    download,
    fetch_bid_document,
    parent_document_path,
)
from gem_agent.tools.gem.models import Bid
from gem_agent.tools.http_client import BidplusSession, ClientPool, HostNotAllowed
from tests.conftest import fixture_bytes

B = "https://bidplus.gem.gov.in"


def bid(**kw):
    base = {"b_id": 10037444, "bid_number": "GEM/2026/B/8162911", "title": "t", "bid_type": 1}
    return Bid(**(base | kw))


@pytest.fixture
def pool(gem, clock):
    return ClientPool(transport=gem.transport(), clock=clock, sleep=clock.sleep)


def test_bid_document_route():
    assert document_path(bid()) == "/showbidDocument/10037444"


def test_reverse_auction_routes():
    assert document_path(bid(b_id=7, bid_type=2)) == "/showradocumentPdf/7"
    assert document_path(bid(b_id=7, bid_type=2, eval_type=1)) == "/list-ra-schedules/7"
    assert document_path(bid(b_id=7, bid_type=5)) == "/showdirectradocumentPdf/7"


def test_reverse_auction_points_to_its_original_bid():
    assert parent_document_path(bid(b_id=7, bid_type=2, parent_b_id=9471263)) == "/showbidDocument/9471263"
    assert parent_document_path(bid()) is None


async def test_fetch_bid_document_is_a_pdf(gem, pool):
    gem.add("GET", f"{B}/showbidDocument/10037444", "doc_bid.pdf")
    doc = await fetch_bid_document(BidplusSession(pool), bid())
    assert doc.kind == "pdf" and doc.size == 104437


async def test_reverse_auction_via_bid_route_is_empty(gem, pool):
    gem.add("GET", f"{B}/showbidDocument/10170908", "doc_ra_via_bid_route.pdf")
    doc = await download(pool, f"{B}/showbidDocument/10170908")
    assert doc.kind == "empty" and doc.size == 0


async def test_html_attachment_is_kept_as_html(gem, pool):
    # bidsla and showCatalogue are real documents served as HTML pages (SLA text, item specification).
    gem.add("GET", f"{B}/bidding/bid/bidsla/100750282053136", "att_bidsla.html.gz")
    doc = await download(pool, f"{B}/bidding/bid/bidsla/100750282053136")
    assert doc.kind == "html"


async def test_filename_from_content_disposition(gem, pool):
    url = f"{B}/bidding/bid/downloadBuyerDoc/10037444/17919884556139.pdf"
    gem.add("GET", url, lambda r: httpx.Response(200, content=b"%PDF-1.7 x", headers={
        "content-type": "application/pdf", "content-disposition": 'attachment; filename="buyer.pdf"'}))
    doc = await download(pool, url)
    assert doc.filename == "buyer.pdf" and doc.kind == "pdf"


async def test_filename_falls_back_to_url(gem, pool):
    url = f"{B}/resources/upload_nas/OctQ426/bidding/biddoc/bid-10037444/1791988256.pdf"
    gem.add("GET", url, lambda r: httpx.Response(200, content=b"%PDF-1.7 x"))
    assert (await download(pool, url)).filename == "1791988256.pdf"


async def test_archive_is_recognised(gem, pool):
    gem.add("GET", f"{B}/a.zip", lambda r: httpx.Response(200, content=b"PK\x03\x04rest"))
    assert (await download(pool, f"{B}/a.zip")).kind == "archive"


async def test_download_refuses_hosts_outside_allowlist(pool):
    with pytest.raises(HostNotAllowed):
        await download(pool, "http://www.tcpdf.org")


def test_attachment_links_from_bid_pdf(pool):
    links = attachment_links(fixture_bytes("doc_bid.pdf"), allowed=pool.is_allowed)
    assert [link.url for link in links] == [
        "https://bidplus.gem.gov.in/resources/upload_nas/OctQ426/bidding/biddoc/bid-10037444/1791988256.pdf",
        "https://bidplus.gem.gov.in/resources/upload_nas/OctQ426/bidding/biddoc/bid-10037444/1791988264.pdf",
        "https://bidplus.gem.gov.in/bidding/bid/downloadBuyerDoc/10037444/17919884556139.pdf",
        ("https://fulfilment.gem.gov.in/contract/slafds?fileDownloadPath=SLA_UPLOAD_PATH/2026/Oct/GEM_2026_B_8162911/"
         "CLM0012/MUSKANrfp_204a5437-888b-4220-88a91791991366321_CO_PROCUREMENT_BUYER20.pdf"),
        "https://admin.gem.gov.in/apis/v1/gtc/pdfByDate/?date=20261014",
        "https://bidplus.gem.gov.in/bidding/bid/bidsla/100750282053136",
    ]
    assert [link.page for link in links] == [4, 4, 4, 5, 6, 6]
    assert links[4].kind == "gtc" and links[0].kind == "document"


def test_ra_pdf_links_to_parent_bid_and_drops_outside_hosts(pool):
    links = attachment_links(fixture_bytes("doc_ra.pdf"), allowed=pool.is_allowed)
    assert [(link.kind, link.url) for link in links] == [
        ("parent_bid", "https://bidplus.gem.gov.in/showbidDocument/9471263"),
        ("gtc", "https://admin.gem.gov.in/apis/v1/gtc/pdfByDate/?date=20261116"),
    ]


def test_attachment_links_of_empty_pdf_is_empty(pool):
    assert attachment_links(b"", allowed=pool.is_allowed) == []


def test_office_files_are_not_archives():
    from gem_agent.tools.gem.documents import classify
    from tests.conftest import doc_bytes

    assert classify(doc_bytes("boq.xlsx")) == "office"
    assert classify(doc_bytes("terms.docx")) == "office"


def test_plain_zip_is_an_archive():
    import io
    import zipfile

    from gem_agent.tools.gem.documents import classify

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("rfp.pdf", b"%PDF-1.4")
    assert classify(buf.getvalue()) == "archive"
