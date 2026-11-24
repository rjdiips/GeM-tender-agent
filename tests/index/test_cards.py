def test_a_heading_repeated_on_every_page_does_not_crowd_out_the_title():
    # a running header such as "Scope of Work - RFP for IT Services" on pages 4-15 must be kept once, not 12 times
    from gem_agent.index.cards import _attachment_text
    from gem_agent.tools.pdf.text import PageText

    pages = [PageText(page=n, text="Disclaimer and contents", method="text") for n in range(1, 4)]
    pages += [PageText(page=n, text="Scope of Work - RFP for IT Services of the Bank\nclause text", method="text")
              for n in range(4, 16)]
    pages += [PageText(page=n, text="annexure", method="text") for n in range(16, 20)]
    pages.append(PageText(page=20, text="Request for Proposal for Selection of SI for Loan Origination System", method="text"))
    text = _attachment_text(pages).split("... lines further in:")[1]
    assert text.count("Scope of Work - RFP for IT Services of the Bank") == 1
    assert "(p.20) Request for Proposal for Selection of SI for Loan Origination System" in text


def test_scope_sections_skip_contents_entries_and_sentences_that_mention_the_scope():
    from gem_agent.index.cards import _scope_sections
    from gem_agent.tools.pdf.text import PageText

    texts = ["cover", "Scope of Work ........ 9\nAnnexure-1 (3.3 Broad Scope of Work) ..... 16",
             "1. Scope of Work is attached vide Annexure-I", "x", "x", "x", "x", "x",
             "8. Scope of Work\n8.1 Supply of the Loan Origination System", "x " * 50, "x", "x", "x", "x", "x",
             "ANNEXURE I - Broad Scope of Work\na. Functionalities related to CBS / DMS"]
    pages = [PageText(page=n, text=t, method="text") for n, t in enumerate(texts, 1)]
    out = _scope_sections(pages)
    assert "(p.9) 8. Scope of Work\n8.1 Supply of the Loan Origination System" in out
    assert "(p.16) ANNEXURE I - Broad Scope of Work\na. Functionalities" in out
    assert "attached vide" not in out and "....." not in out


def test_a_long_run_of_ocr_digits_does_not_stall_the_scope_search():
    # Backtracking over ways to split the digits into section numbers like "3.2" can take minutes; the regex
    # engine holds the GIL, so the check runs in a child process that a timeout can kill.
    import subprocess
    import sys

    code = """
from gem_agent.index.cards import _scope_sections
from gem_agent.tools.pdf.text import PageText
noise = "3333883233833 322232333833333383333333333333333338"
texts = ["cover", noise + "\\n" + "1" * 400 + "\\n" + "1." * 300 + "x", "x", "x",
         "3.2 Scope of Work\\nAML transaction monitoring"]
print(_scope_sections([PageText(page=n, text=t, method="text") for n, t in enumerate(texts, 1)]))
"""
    try:
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=20, check=True).stdout
    except subprocess.TimeoutExpired:
        raise AssertionError("the scope search was still running after 20 s") from None
    assert "(p.5) 3.2 Scope of Work\nAML transaction monitoring" in out


def test_scope_headings_keep_matching_with_numbering_and_annexure_prefixes():
    from gem_agent.index.cards import _SCOPE_HEADING

    for heading in ("3.2 Scope of Work", "5.1.2. SCOPE OF WORK AND DELIVERABLES", "iv. Scope of the Project",
                    "a) Detailed Scope of Services", "ii. a) 3.1 Scope of Work", "Annexure-II: Scope of Work",
                    "ANNEXURE I - Broad Scope of Work", "8. Scope of Work:", "1 2 3 Scope of work"):
        assert _SCOPE_HEADING.match(heading), heading
    for sentence in ("Scope of Work is attached vide Annexure-I", "The scope of work shall include"):
        assert not _SCOPE_HEADING.match(sentence), sentence
