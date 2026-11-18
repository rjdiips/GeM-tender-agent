import io
import zipfile

import pytest

from gem_agent.tools.archive import ArchiveError, safe_members
from gem_agent.tools.office import docx_text, html_text, xlsx_text
from tests.conftest import doc_bytes, fixture_bytes


def test_html_text_drops_scripts_and_keeps_content():
    pages = html_text(fixture_bytes("att_bidsla.html.gz"))
    assert len(pages) == 1
    assert "Service Provider" in pages[0].text
    assert "$.ajax" not in pages[0].text and "function (" not in pages[0].text


def test_docx_text_includes_paragraphs_and_tables():
    pages = docx_text(doc_bytes("terms.docx"))
    text = " ".join(p.text for p in pages)
    assert "PUNJAB NATIONAL BANK" in text and "CBS Upgradation" in text
    assert len(text) > 5000  # 73 tables' worth


def make_docx(build):
    import docx
    document = docx.Document()
    build(document)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def test_docx_is_split_at_its_page_breaks_with_tables_in_place():
    # 8253403 (16 Nov): a 354,000-character RFP was one page, and the model's p.2 and p.3 quotes failed the check
    from docx.enum.text import WD_BREAK

    def build(d):
        d.add_paragraph("1. Introduction")
        d.add_paragraph("The bank intends to on-board a vendor").add_run().add_break(WD_BREAK.PAGE)
        table = d.add_table(rows=1, cols=2)
        table.cell(0, 0).text, table.cell(0, 1).text = "EMD", "Rs 10,00,000"
        d.add_paragraph("2. Scope of work")
    pages = docx_text(make_docx(build))
    assert [p.page for p in pages] == [1, 2]
    assert "on-board a vendor" in pages[0].text
    assert pages[1].text.splitlines() == ["EMD | Rs 10,00,000", "2. Scope of work"]  # the table where it stands


def test_docx_without_page_marks_is_cut_into_parts_of_about_a_page():
    pages = docx_text(make_docx(lambda d: [d.add_paragraph(f"Clause {n}: " + "terms " * 30) for n in range(100)]))
    assert len(pages) > 3 and all(len(p.text) <= 3300 for p in pages)
    assert pages[1].text.startswith("Clause")  # cut between lines, not inside one


def test_xlsx_text_one_page_per_sheet():
    pages = xlsx_text(doc_bytes("boq.xlsx"))
    assert pages[0].page == 1 and "Indicative Commercial Bid" in pages[0].text


def test_broken_office_file_gives_no_pages():
    assert docx_text(b"PK\x03\x04broken") == [] and xlsx_text(b"junk") == []


def zip_of(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_archive_members_are_listed_and_read():
    members = safe_members(zip_of({"rfp/scope.pdf": b"%PDF-1.4 a", "boq.xlsx": b"x"}))
    assert [m.name for m in members] == ["rfp/scope.pdf", "boq.xlsx"]
    assert members[0].content == b"%PDF-1.4 a"


def test_archive_paths_outside_the_folder_are_rejected():
    members = safe_members(zip_of({"../../etc/passwd": b"x", "/abs.pdf": b"y", "ok.pdf": b"z"}))
    assert [m.name for m in members] == ["ok.pdf"]


def test_archive_size_limit():
    with pytest.raises(ArchiveError):
        safe_members(zip_of({"big.bin": b"0" * 5000}), max_total_bytes=1000)


def test_directories_are_skipped():
    assert [m.name for m in safe_members(zip_of({"dir/": b"", "dir/a.pdf": b"%PDF"}))] == ["dir/a.pdf"]
