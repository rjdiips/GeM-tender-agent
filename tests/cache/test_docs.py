from datetime import datetime

from gem_agent.cache.docs import BidFiles, DocCache, FileRecord
from gem_agent.tools.pdf.text import PageText


def test_file_stored_once_by_content_hash(tmp_path):
    cache = DocCache(tmp_path)
    sha1, path1 = cache.store_file(7, b"%PDF-1.4 same", ".pdf")
    sha2, path2 = cache.store_file(7, b"%PDF-1.4 same", ".pdf")
    assert (sha1, path1) == (sha2, path2)
    assert path1 == f"docs/7/files/{sha1}.pdf" and (tmp_path / path1).read_bytes() == b"%PDF-1.4 same"


def test_text_round_trip(tmp_path):
    cache = DocCache(tmp_path)
    _, path = cache.store_file(7, b"x", ".pdf")
    pages = [PageText(page=1, text="scope of work", method="text"), PageText(page=2, text="ocr", method="ocr")]
    text_path = cache.store_text(path, pages)
    record = FileRecord(role="attachment", url="u", filename="a.pdf", size=1, kind="pdf", status="ok",
                        path=path, text_path=text_path)
    assert cache.load_text(record) == pages


def test_gtc_shared_by_date(tmp_path):
    cache = DocCache(tmp_path)
    assert cache.gtc("20261014") is None
    path = cache.store_gtc("20261014", b"%PDF terms")
    assert path == "gtc/20261014.pdf" and cache.gtc("20261014") == path


def test_manifest_round_trip(tmp_path):
    cache = DocCache(tmp_path)
    files = BidFiles(b_id=7, bid_number="GEM/2026/B/7", fetched_at=datetime(2026, 11, 16, 13), corrigendum_flag=False,
                     files=[FileRecord(role="bid_document", url="u", filename="7.pdf", size=3, kind="pdf",
                                       status="ok", pages=2)])
    cache.save(files)
    assert DocCache(tmp_path).load(7) == files
    assert cache.load(8) is None
