import json

import httpx
import pytest

from gem_agent.agents.collector import Collector
from gem_agent.cache.docs import DocCache
from gem_agent.tools.gem.models import Bid
from gem_agent.tools.http_client import BidplusSession, ClientPool
from tests.conftest import doc_bytes, fixture_bytes, make_pdf, make_pdf_with_links

B = "https://bidplus.gem.gov.in"
UPLOAD = f"{B}/resources/upload_nas/OctQ426/bidding/biddoc/bid-10037444"
GTC = "https://admin.gem.gov.in/apis/v1/gtc/pdfByDate/"
TERMS = make_pdf("General terms and conditions of GeM applicable to every bid on this date. " * 3)


def ok(content: bytes, **headers):
    return lambda request: httpx.Response(200, content=content, headers=headers)


def flags(corrigendum: bool):
    body = {"status": 1, "code": 200, "response": {"corrigendum": corrigendum, "representation": False}}
    return lambda request: httpx.Response(200, text=json.dumps(body))


@pytest.fixture
def site(gem):
    gem.add("GET", f"{B}/advance-search", "advance_search.html.gz")
    gem.add("POST", f"{B}/public-bid-other-details/10037444", flags(True))
    gem.add("POST", f"{B}/bidding/bid/viewCorrigendum/10037444", "corrigendum.html")
    gem.add("GET", f"{B}/showbidDocument/10037444", "doc_bid.pdf")
    gem.add("GET", f"{UPLOAD}/1791988256.pdf", ok(doc_bytes("placeholder_refer_rfp.pdf")))
    gem.add("GET", f"{UPLOAD}/1791988264.pdf", ok(doc_bytes("rfp_firewall.pdf")))
    gem.add("GET", f"{B}/bidding/bid/downloadBuyerDoc/10037444/17919884556139.pdf", ok(doc_bytes("terms.docx")))
    gem.add("GET", "https://fulfilment.gem.gov.in/contract/slafds", lambda r: httpx.Response(503))
    gem.add("GET", GTC, ok(TERMS))
    gem.add("GET", f"{B}/bidding/bid/bidsla/100750282053136", ok(fixture_bytes("att_bidsla.html.gz")))
    return gem


@pytest.fixture
def collector(site, clock, tmp_path):
    session = BidplusSession(ClientPool(transport=site.transport(), clock=clock, sleep=clock.sleep))
    return Collector(session, DocCache(tmp_path), ocr=lambda pdf, i: "")


BID = Bid(b_id=10037444, bid_number="GEM/2026/B/8162911", title="t", bid_type=1)


def by_url(files, part):
    return next(f for f in files.files if part in f.url)


def paths(gem):
    return [r.url.path for r in gem.requests]


async def test_collects_bid_document_and_every_attachment(collector):
    files = await collector.collect(BID)
    assert files.files[0].role == "bid_document" and files.files[0].kind == "pdf" and files.files[0].pages == 7
    assert by_url(files, "1791988256").status == "placeholder"
    rfp = by_url(files, "1791988264")
    assert rfp.status == "ok" and rfp.role == "attachment" and rfp.source_page == 4
    assert "FIREWALL" in " ".join(p.text for p in collector.cache.load_text(rfp)).upper()
    assert by_url(files, "downloadBuyerDoc").kind == "office"
    assert by_url(files, "bidsla").kind == "html" and by_url(files, "bidsla").status == "ok"
    gtc = by_url(files, "gtc")
    assert gtc.role == "gtc" and gtc.path == "gtc/20261014.pdf" and gtc.pages == 1


async def test_failed_attachment_is_recorded_and_others_kept(collector):
    files = await collector.collect(BID)
    sla = by_url(files, "slafds")
    assert sla.status == "failed" and "503" in sla.error
    assert sum(f.status == "ok" for f in files.files) >= 4


async def test_corrigendum_is_stored_with_the_bid(collector):
    files = await collector.collect(BID)
    assert files.corrigendum_flag and files.corrigendum.extended_to.day == 23


async def test_manifest_survives_a_restart(collector, tmp_path):
    files = await collector.collect(BID)
    assert DocCache(tmp_path).load(BID.b_id) == files


async def test_cached_bid_without_new_corrigendum_is_not_downloaded_again(collector, site):
    await collector.collect(BID)
    site.requests.clear()
    await collector.collect(BID)
    assert paths(site) == ["/public-bid-other-details/10037444", "/bidding/bid/viewCorrigendum/10037444"]


async def test_cached_bid_without_any_corrigendum_costs_one_request(site, clock, tmp_path):
    site.add("POST", f"{B}/public-bid-other-details/10037444", flags(False))
    session = BidplusSession(ClientPool(transport=site.transport(), clock=clock, sleep=clock.sleep))
    collector = Collector(session, DocCache(tmp_path), ocr=lambda pdf, i: "")
    first = await collector.collect(BID)
    assert first.corrigendum is None
    site.requests.clear()
    await collector.collect(BID)
    assert paths(site) == ["/public-bid-other-details/10037444"]


async def test_new_corrigendum_entry_triggers_refetch(collector, site):
    await collector.collect(BID)
    newer = fixture_bytes("corrigendum.html").decode().replace("2026-11-09 16:48:47", "2026-11-14 10:00:00")
    site.add("POST", f"{B}/bidding/bid/viewCorrigendum/10037444", lambda r: httpx.Response(200, text=newer))
    site.requests.clear()
    await collector.collect(BID)
    assert "/showbidDocument/10037444" in paths(site)


async def test_gtc_downloaded_once_per_date(collector, site):
    await collector.collect(BID)
    await collector.collect(BID, force=True)
    assert paths(site).count("/apis/v1/gtc/pdfByDate/") == 1


async def test_reverse_auction_follows_its_original_bid(gem, clock, tmp_path):
    parent = make_pdf_with_links("Original bid for supply of servers with installation",
                                 [f"{B}/resources/upload_nas/X/bidding/biddoc/bid-9471263/1.pdf"])
    gem.add("GET", f"{B}/advance-search", "advance_search.html.gz")
    gem.add("POST", f"{B}/public-bid-other-details/10170908", flags(False))
    gem.add("GET", f"{B}/showradocumentPdf/10170908", "doc_ra.pdf")
    gem.add("GET", f"{B}/showbidDocument/9471263", ok(parent))
    gem.add("GET", f"{B}/resources/upload_nas/X/bidding/biddoc/bid-9471263/1.pdf", ok(doc_bytes("rfp_firewall.pdf")))
    gem.add("GET", GTC, ok(TERMS))
    session = BidplusSession(ClientPool(transport=gem.transport(), clock=clock, sleep=clock.sleep))
    ra = Bid(b_id=10170908, bid_number="GEM/2026/R/770918", title="t", bid_type=2, parent_b_id=9471263)
    files = await Collector(session, DocCache(tmp_path), ocr=lambda pdf, i: "").collect(ra)
    roles = [(f.role, f.url.rsplit("/", 2)[-2:]) for f in files.files]
    assert roles[0] == ("bid_document", ["showradocumentPdf", "10170908"])
    assert ("parent_document", ["showbidDocument", "9471263"]) in roles
    assert ("attachment", ["bid-9471263", "1.pdf"]) in roles
    assert paths(gem).count("/showbidDocument/9471263") == 1


async def test_scanned_pages_go_to_ocr(gem, clock, tmp_path):
    gem.add("GET", f"{B}/advance-search", "advance_search.html.gz")
    gem.add("POST", f"{B}/public-bid-other-details/5", flags(False))
    gem.add("GET", f"{B}/showbidDocument/5", ok(make_pdf_with_links(
        "Bid document with a scanned RFP attached to it here", [f"{B}/resources/upload_nas/X/scan.pdf"])))
    gem.add("GET", f"{B}/resources/upload_nas/X/scan.pdf", ok(make_pdf("", "")))
    session = BidplusSession(ClientPool(transport=gem.transport(), clock=clock, sleep=clock.sleep))
    calls = []
    collector = Collector(session, DocCache(tmp_path), ocr=lambda pdf, i: calls.append(i) or "cloud hosting scope")
    files = await collector.collect(Bid(b_id=5, bid_number="GEM/2026/B/5", title="t", bid_type=1))
    scan = by_url(files, "scan.pdf")
    assert calls == [0, 1] and scan.ocr_pages == [1, 2] and scan.status == "ok"


async def test_archive_members_become_files(gem, clock, tmp_path):
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("rfp/scope.pdf", doc_bytes("rfp_firewall.pdf"))
        z.writestr("../evil.pdf", b"%PDF")
    gem.add("GET", f"{B}/advance-search", "advance_search.html.gz")
    gem.add("POST", f"{B}/public-bid-other-details/6", flags(False))
    gem.add("GET", f"{B}/showbidDocument/6", ok(make_pdf_with_links(
        "Bid document with an archive attached to it here", [f"{B}/resources/upload_nas/X/docs.zip"])))
    gem.add("GET", f"{B}/resources/upload_nas/X/docs.zip", ok(buf.getvalue()))
    session = BidplusSession(ClientPool(transport=gem.transport(), clock=clock, sleep=clock.sleep))
    files = await Collector(session, DocCache(tmp_path), ocr=None).collect(
        Bid(b_id=6, bid_number="GEM/2026/B/6", title="t", bid_type=1))
    member = next(f for f in files.files if f.role == "archive_member")
    assert member.filename == "rfp/scope.pdf" and member.status == "ok" and member.kind == "pdf"
    assert not any("evil" in f.filename for f in files.files)


async def test_bid_document_only_first_then_attachments(collector, site):
    first = await collector.collect(BID, attachments=False)
    assert [f.role for f in first.files] == ["bid_document"] and first.partial
    assert not any("upload_nas" in str(r.url) for r in site.requests)
    site.requests.clear()
    full = await collector.collect(BID)
    assert not full.partial and len(full.files) == 7
    assert "/showbidDocument/10037444" not in paths(site)  # the bid PDF is reused, not fetched again


async def test_partial_cache_serves_a_partial_request(collector, site):
    await collector.collect(BID, attachments=False)
    site.requests.clear()
    again = await collector.collect(BID, attachments=False)
    assert again.partial and "/showbidDocument/10037444" not in paths(site)


async def test_full_cache_serves_a_partial_request(collector, site):
    await collector.collect(BID)
    site.requests.clear()
    assert not (await collector.collect(BID, attachments=False)).partial
    assert not any(p.startswith("/resources") for p in paths(site))


async def test_cached_files_get_the_buyer_org_when_it_is_missing(tmp_path, clock):
    from gem_agent.cache.docs import BidFiles
    from tests.graph.fakes import bid as fake_bid

    cache = DocCache(tmp_path)
    sha, path = cache.store_file(7, b"%PDF old", ".pdf")
    tp = cache.store_text(path, [__import__("gem_agent.tools.pdf.text", fromlist=["PageText"]).PageText(
        page=1, text="Organisation Name\nUnion Bank Of India (ubi)", method="text")])
    from gem_agent.cache.docs import FileRecord
    old = BidFiles(b_id=7, bid_number="GEM/2026/B/7", fetched_at=__import__("datetime").datetime(2026, 11, 16),
                   corrigendum_flag=False, files=[FileRecord(role="bid_document", url="u", filename="7.pdf", size=5,
                                                             kind="pdf", status="ok", sha256=sha, path=path,
                                                             text_path=tp, pages=1)])
    cache.save(old)
    site = type("Site", (), {"transport": None})()
    session = BidplusSession(ClientPool(transport=site.transport, clock=clock, sleep=clock.sleep))
    collector = Collector(session, cache, ocr=None)
    files = await collector.collect(fake_bid(7))
    assert files.buyer_org == "Union Bank Of India (ubi)"
    assert DocCache(tmp_path).load(7).buyer_org == "Union Bank Of India (ubi)"


async def test_failed_corrigendum_check_still_reads_the_bid(site, clock, tmp_path):
    site.add("POST", f"{B}/public-bid-other-details/10037444", lambda r: httpx.Response(403))
    session = BidplusSession(ClientPool(transport=site.transport(), clock=clock, sleep=clock.sleep))
    collector = Collector(session, DocCache(tmp_path), ocr=lambda pdf, i: "")
    files = await collector.collect(BID)
    assert files.main_ok and files.corrigendum is None and not files.corrigendum_flag


async def test_failed_corrigendum_check_serves_the_cache(collector, site):
    first = await collector.collect(BID)
    site.add("POST", f"{B}/public-bid-other-details/10037444", lambda r: httpx.Response(403))
    site.requests.clear()
    again = await collector.collect(BID)
    assert again.corrigendum == first.corrigendum and again.files == first.files
    assert "/showbidDocument/10037444" not in paths(site)
