"""File names people can read: the results showed "1793961738.pdf" nine times over for 10115089, and five
of its nine files were one RFP uploaded into every slot."""
from gem_agent.cache.docs import DocCache
from gem_agent.cache.names import display_files, doc_title, download_name, ensure_named, name_files
from gem_agent.tools.pdf.text import PageText
from tests.agents.helpers import bid_files

RFP = [("RFP- 02/2026-27 for Supply, Installation, Commissioning and\nMaintenance of Enterprise Fraud Risk Management "
        "Solution (EFRMS)\nPage 1 of 226\n1. Introduction"), "clauses"]
BID_DOC = ["Bid Number GEM/2026/B/8229813\nItem Category Hiring of Agency for IT Projects", "dates",
           "Technical evaluation", ("Scope of Work:scope.pdf Payment Terms:p1.pdf\nPre-Qualification Criteria:p2.pdf\n"
                                    "Instructions to Bidder:p3.pdf\nAdditional Document 1:p4.pdf")]


def efrms(cache):
    files = bid_files(cache, 7, [
        ("GeM-Bidding-7.pdf", "bid_document", BID_DOC),
        ("scope.pdf", "attachment", ["1. The Broad Scope of this RFP is:\nEFRM, EWS and mule detection"]),
        ("p1.pdf", "attachment", RFP), ("p2.pdf", "attachment", RFP), ("p3.pdf", "attachment", RFP),
        ("p4.pdf", "attachment", RFP), ("RFP02_7c64_buyconmgbit.pdf", "attachment", RFP),
        ("102315025477921", "attachment", ["1-800-419-3436 / 1-800-102-3436\nLogin\nSign Up"]),
    ])
    files.files[-1] = files.files[-1].model_copy(update={
        "kind": "html", "url": "https://bidplus.gem.gov.in/bidding/bid/bidsla/102315025477921"})
    return files


def test_files_are_named_by_their_slot_their_heading_and_copies_shown_once(tmp_path):
    cache = DocCache(tmp_path)
    files = efrms(cache)
    assert name_files(files, cache)
    groups = display_files(files.files)
    shown = [(g.name, len(g.records)) for g in groups]
    assert groups[0].also == [] and groups[1].also == []  # a single file does not repeat its own file name
    assert shown == [
        ("Bid document", 1),
        ("Scope of Work", 1),
        (("RFP- 02/2026-27 for Supply, Installation, Commissioning and Maintenance of Enterprise Fraud Risk "
          "Management Solution (EFRMS)"), 5),  # one RFP in four slots plus an ATC upload: its labels disagree
        ("Service level agreement (GeM)", 1),
    ]
    rfp = display_files(files.files)[2]
    assert rfp.also == ["Payment Terms", "Pre-Qualification Criteria", "Instructions to Bidder",
                        "Additional Document 1", "RFP02_7c64_buyconmgbit.pdf"]


def test_naming_runs_once(tmp_path):
    cache = DocCache(tmp_path)
    files = efrms(cache)
    assert name_files(files, cache) and not name_files(files, cache)


def test_a_weak_slot_label_gives_way_to_the_heading(tmp_path):
    cache = DocCache(tmp_path)
    files = bid_files(cache, 8, [("bid.pdf", "bid_document", ["x", "x", "Additional Document 1:a.pdf"]),
                                 ("a.pdf", "attachment", ["Technical Specifications – AI Driver Training System"])])
    name_files(files, cache)
    assert display_files(files.files)[1].name == "Technical Specifications – AI Driver Training System"


def test_a_file_with_no_label_and_no_heading_keeps_its_file_name(tmp_path):
    cache = DocCache(tmp_path)
    files = bid_files(cache, 9, [("bid.pdf", "bid_document", ["x"]), ("17939649313319.pdf", "attachment", ["12 34"])])
    name_files(files, cache)
    group = display_files(files.files)[1]
    assert group.name == "17939649313319.pdf"
    assert download_name("GEM/2026/B/9", group) == "GEM-2026-B-9 - 17939649313319.pdf"  # not ".pdf.pdf"


def page(text):
    return [PageText(page=1, text=text, method="text")]


def test_the_heading_skips_letterheads_page_numbers_portal_menus_and_ocr_noise():
    assert doc_title(page("1 | P a g e\nREQUEST FOR EMPANELMENT (RFE)\nOF AGENCIES FOR PRINTING & MERCHANDISE ITEMS\n"
                          "FOR DIGITAL INDIA PROGRAM\nRFE No: ……")) == (
        "REQUEST FOR EMPANELMENT (RFE) OF AGENCIES FOR PRINTING & MERCHANDISE ITEMS FOR DIGITAL INDIA PROGRAM")
    assert doc_title(page("BHARAT PETROLEUM CORPORATION LIMITED\nRETAIL HQ\nLIMITED TENDER\n"
                          "CONSULTANCY SERVICES FOR TPAP AND BBPS-AI")).startswith("LIMITED TENDER")
    assert doc_title(page("Login\nSign Up\nBuyers\nSellers")) is None
    assert doc_title(page("\\\nwere atfereantise restr ese\n12 34")) is None


def test_a_heading_stops_where_ocr_noise_begins():
    # 10164102: a Hindi letter read as Latin; the heading ran on into "wa Ulead a AIA a ae oA ae Tfter wet fd…"
    title = doc_title(page("Tender Document dated 15.10.2026 ® Terms and Conditions for Empanelment of\n"
                           "Digital Forensic and Data Analysis wa Ulead a AIA a ae oA ae\nTfter wet fd ard gaa"))
    assert title == "Tender Document dated 15.10.2026 Terms and Conditions for Empanelment of Digital Forensic and Data Analysis"
    # dashes, slashes, "be", "up" and OCR's "Al" for AI are not noise
    assert doc_title(page("Price Bid - Financial Document / Annexure: price break up to be filled for Al server")) == (
        "Price Bid - Financial Document / Annexure: price break up to be filled for Al server")


def test_a_long_heading_is_cut_at_a_word():
    title = doc_title(page("Request for Proposal for " + "very long words " * 20))
    assert title is not None and len(title) <= 161 and title.endswith("…") and not title.endswith(" …")


def test_download_names_carry_the_bid_number_and_an_extension(tmp_path):
    cache = DocCache(tmp_path)
    files = efrms(cache)
    name_files(files, cache)
    groups = display_files(files.files)
    assert download_name("GEM/2026/B/8229813", groups[1]) == "GEM-2026-B-8229813 - Scope of Work.pdf"
    assert download_name("GEM/2026/B/8229813", groups[3]) == "GEM-2026-B-8229813 - Service level agreement (GeM).html"


def test_records_saved_before_naming_get_names_from_the_cache(tmp_path):
    # a run's stored outcome keeps the file list from when it ran; the cached manifest is named on first view
    cache = DocCache(tmp_path)
    files = efrms(cache)
    cache.save(files)
    old = [r.model_copy() for r in files.files]
    named = ensure_named(7, old, cache)
    assert display_files(named)[1].name == "Scope of Work"
    assert cache.load(7).files[1].label == "Scope of Work"  # saved, so the next view reads it


def test_different_texts_under_one_heading_are_numbered_versions(tmp_path):
    # 10115089: two uploads of "RFP- 02/2026-27 …" differ on 3 pages (technical score out of 300, then 100)
    cache = DocCache(tmp_path)
    v1, v2 = RFP + ["Total Score for Technical Evaluation is 300 marks"], RFP + ["Total Score ... is 100 marks"]
    files = bid_files(cache, 10, [("bid.pdf", "bid_document", ["x"]), ("1793964451.pdf", "attachment", v2),
                                  ("1793961738.pdf", "attachment", v1), ("1793961743.pdf", "attachment", v1)])
    name_files(files, cache)
    names = [(g.name[-11:], len(g.records)) for g in display_files(files.files)]
    assert names == [("Bid document"[-11:], 1), ("(version 2)", 1), ("(version 1)", 2)]
    downloads = {download_name("GEM/2026/B/10", g) for g in display_files(files.files)}
    assert len(downloads) == 3 and any(d.endswith("(version 2).pdf") for d in downloads)  # never one name for two


def test_gem_custom_bid_slots_wrapped_over_two_lines_get_their_full_label(tmp_path):
    # 8013322: the label was read from its last line only, "functionality" and "download standard … upload"
    cache = DocCache(tmp_path)
    bid_text = ("Excel Upload Required :\nPRICE BREAKUP - b0.xlsx\nAdditional Qualification/Data Required\n"
                "As per RFP\nScope of Work:b1.pdf\n"
                "Undertaking of Competent Authority is mandatory to create Custom Bid for Services. Please\n"
                "download standard format document and upload:b2.pdf\n"
                "Introduction about the project /services being proposed for procurement using custom bid\n"
                "functionality:b3.pdf\nPre Qualification Criteria ( PQC ) etc if any required:b4.pdf\n"
                "Document Matrix describing period of hiring of different consultants along with corresponding start\n"
                "and end date of engagement duration:b5.pdf\n")
    names = ["b0.xlsx", "b1.pdf", "b2.pdf", "b3.pdf", "b4.pdf", "b5.pdf"]
    files = bid_files(cache, 11, [("bid.pdf", "bid_document", [bid_text])]
                      + [(n, "attachment", [f"text of {n}"]) for n in names])
    files.files.append(files.files[-1].model_copy(update={
        "filename": "downloadOmppdfile", "status": "empty", "kind": "empty", "size": 0, "text_path": None}))
    name_files(files, cache)
    assert [g.name for g in display_files(files.files)] == [  # GeM's empty link is left out
        "Bid document", "PRICE BREAKUP", "Scope of Work", "Undertaking of Competent Authority", "Project introduction",
        "Pre Qualification Criteria (PQC)",
        "Document Matrix describing period of hiring of different…"]


def test_an_annexure_marker_heads_the_line_after_it():
    # 8253403: "Annexure-10" alone was too short to be a heading, so the file showed as 17944654137245.pdf
    assert doc_title(page("Annexure-10\nTechnical Evaluation Criteria\n(Should be submitted on Company's letter head)")) \
        == "Annexure-10 Technical Evaluation Criteria"


def test_an_approval_note_is_named_by_its_subject_line_not_its_letterhead():
    assert doc_title(page("Bank of india\nBANK OF INDIA MEMO NO.HO: EST: 2026-27:\nESTATE DEPARTMENT DATE: 14.10.2026\n"
                          "Sub: In principle approval for purchase of 60 Godrej Interio Bravo Mid Back\n\nChairs")) == (
        "In principle approval for purchase of 60 Godrej Interio Bravo Mid Back")
    assert doc_title(page("STATE BANK OF INDIA\nREGIONAL BUSINESS OFFICE, TIRUCHIRAPPALLI (14767)\nDate: 27.10.2026\n"
                          "Note for Purchase of Karatometer through GeM Portal for Region 1 Branches\n\nIt has been decided")) \
        == "Note for Purchase of Karatometer through GeM Portal for Region 1 Branches"


def test_a_bracketed_continuation_stays_in_the_heading_but_an_instruction_does_not():
    assert doc_title(page("RFP for NCMC digital ticketing and implementation of Intelligent Transport Management\n"
                          "(ITMS) for JKRTC.\n\nJKRTC – NCMC Digital Ticketing & ITMS RFP Page 2 of 166")) == (
        "RFP for NCMC digital ticketing and implementation of Intelligent Transport Management (ITMS) for JKRTC")
    assert doc_title(page("INTEGRITY PACT\n(To be stamped as per the Stamp Law of the Respective State)\nBetween UCO Bank")) \
        == "INTEGRITY PACT"


def test_a_heading_stops_at_a_subject_line_a_salutation_or_another_heading_in_capitals():
    assert doc_title(page("Annexure-X\nINTEGRITY PACT\n(To be stamped as per the Stamp Law)")) == "Annexure-X INTEGRITY PACT"
    assert doc_title(page("Annexure-10\nTechnical Evaluation Criteria\nSUB: Selection of Vendor for RBI reporting")) == (
        "Annexure-10 Technical Evaluation Criteria")
    assert doc_title(page("Sub: Website Design, Development and Maintenance Services\nRespected Sir/Madam,\nPayment Terms")) \
        == "Website Design, Development and Maintenance Services"
