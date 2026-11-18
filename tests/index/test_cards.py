from datetime import datetime

from gem_agent.cache.docs import DocCache
from gem_agent.index.cards import CARD_INSTRUCTIONS, write_cards
from gem_agent.index.db import BidIndex
from gem_agent.tools.gem.models import Bid
from tests.agents.helpers import ScriptedLLM, bid_files
from tests.index.helpers import row

VAGUE = "Hiring of Agency for IT Projects- Milestone basis"


class PartialCollector:
    def __init__(self, cache, pages, fail=()):
        self.cache, self.pages, self.fail, self.calls = cache, pages, set(fail), []

    async def collect(self, bid, *, force=False, attachments=True):
        self.calls.append((bid.b_id, attachments))
        if bid.b_id in self.fail:
            raise RuntimeError("GeM 503")
        return bid_files(self.cache, bid.b_id, [("bid.pdf", "bid_document", self.pages)])


def setup(tmp_path, n=2):
    index = BidIndex(":memory:")
    at = datetime(2026, 11, 10)
    index.upsert([Bid.from_listing(row(i, at, datetime(2026, 11, 28), VAGUE)) for i in range(1, n + 1)], seen_at=at)
    return index, DocCache(tmp_path)


async def test_each_bid_is_read_with_its_attachments_in_one_model_call(tmp_path):
    index, cache = setup(tmp_path)
    collector = PartialCollector(cache, ["Bid details. Item: Implementation of MSME Digital Lending Suite for the bank"])
    llm = ScriptedLLM({CARD_INSTRUCTIONS: [{"subject": "MSME Digital Lending Suite implementation for a bank",
                                            "terms": ["digital lending", "loan origination"]}]})
    report = await write_cards(index, collector, llm.model(), limit=10)
    # always a full collect: the bid PDF can name a broad subject while only the RFP names the modules (16 Nov)
    assert report.written == 2 and collector.calls == [(1, True), (2, True)] and len(llm.calls) == 2
    assert index.card(1) == "MSME Digital Lending Suite implementation for a bank; digital lending; loan origination"
    assert "MSME Digital Lending Suite" in llm.calls[0][1] and VAGUE in llm.calls[0][1]


async def test_a_failed_bid_is_skipped_and_retried_later(tmp_path):
    index, cache = setup(tmp_path)
    collector = PartialCollector(cache, ["text of the bid document for an IT project"], fail={1})
    llm = ScriptedLLM({CARD_INSTRUCTIONS: [{"subject": "Website redesign", "terms": []}]})
    report = await write_cards(index, collector, llm.model(), limit=10)
    assert (report.written, report.failed) == (1, 1)
    assert [b.b_id for b in index.needing_cards()] == [1]


async def test_limit_caps_the_work_per_run(tmp_path):
    index, cache = setup(tmp_path, n=5)
    collector = PartialCollector(cache, ["some bid document text for an IT project"])
    llm = ScriptedLLM({CARD_INSTRUCTIONS: [{"subject": "x project", "terms": []}]})
    assert (await write_cards(index, collector, llm.model(), limit=2)).written == 2
    assert len(index.needing_cards()) == 3


HEADER = "Item Category Hiring of Agency for IT Projects- Milestone basis Contract Period 3 Year(s)"


async def test_the_subject_is_read_from_the_whole_bid_document(tmp_path):
    # 16 Nov: the subject sat on page 4 (buyer-added terms); pages 1-3 only repeat the category
    index, cache = setup(tmp_path, n=1)
    pages = [HEADER, "Bid details and dates", "Scope of Work:1793620055.pdf Payment Terms:1793620061.pdf",
             "Implementation, Maintenance and Customization of MSME Digital Lending Suite with business loan journeys"]
    llm = ScriptedLLM({CARD_INSTRUCTIONS: lambda p: {"subject": "MSME Digital Lending Suite" if "Lending Suite" in p else "",
                                                     "terms": []}})
    await write_cards(index, PartialCollector(cache, pages), llm.model(), limit=1)
    assert index.card(1) == "MSME Digital Lending Suite"


class FullCollector(PartialCollector):
    """attachments=False gives the bid document; a full collect adds the given attachments (name, pages)."""

    def __init__(self, cache, pages, attachments):
        super().__init__(cache, pages)
        self.attachments = attachments

    async def collect(self, bid, *, force=False, attachments=True):
        self.calls.append((bid.b_id, attachments))
        files = [("bid.pdf", "bid_document", self.pages)]
        if attachments:
            files += [(name, "attachment", pages) for name, pages in self.attachments]
        return bid_files(self.cache, bid.b_id, files)


def subject_if(word, subject):
    return lambda p: {"subject": subject if word in p else "", "terms": []}


async def test_rfp_is_read_when_the_bid_document_does_not_say(tmp_path):
    index, cache = setup(tmp_path, n=1)
    pages = [HEADER, "Scope of Work:1793964442.pdf Payment Terms:1793961738.pdf"]
    collector = FullCollector(cache, pages, [("1793961738.pdf", ["Payment milestones"]),
                                             ("1793964442.pdf", ["Supply of an Enterprise Fraud Risk Management Solution (EFRMS)"])])
    llm = ScriptedLLM({CARD_INSTRUCTIONS: subject_if("EFRMS", "Enterprise Fraud Risk Management Solution")})
    await write_cards(index, collector, llm.model(), limit=1)
    assert collector.calls == [(1, True)] and len(llm.calls) == 1
    assert index.card(1) == "Enterprise Fraud Risk Management Solution"
    prompt = llm.calls[-1][1]
    assert prompt.index("1793964442.pdf") < prompt.index("1793961738.pdf")  # the labelled Scope of Work first


async def test_rfp_title_deep_inside_a_large_rfp_is_found(tmp_path):
    # an RFP often opens with disclaimers and a contents list; the line naming the purchase can be pages in
    index, cache = setup(tmp_path, n=1)
    rfp = ["Disclaimer: this document is confidential " * 20] * 39 + [
        "Request for Proposal for Selection of System Integrator for Loan Origination System (LOS) for the Bank"] + ["Annexures"] * 60
    collector = FullCollector(cache, [HEADER], [("rfp.pdf", rfp)])
    llm = ScriptedLLM({CARD_INSTRUCTIONS: subject_if("Loan Origination System (LOS)", "Loan Origination System")})
    await write_cards(index, collector, llm.model(), limit=1)
    assert index.card(1) == "Loan Origination System"


async def test_a_placeholder_scope_document_does_not_hide_the_rfp(tmp_path):
    # 16 Nov: 9 bids had a "Scope of Work" that only said "please refer the RFP"
    index, cache = setup(tmp_path, n=1)
    collector = FullCollector(cache, [HEADER, "Scope of Work:scope.pdf"], [
        ("scope.pdf", ["Please refer the RFP uploaded in Buyer Added Bid Specific ATC"]),
        ("rfp.pdf", ["RFP for a Data Protection Suite for DPDP compliance at the bank", "terms " * 300] * 30)])
    llm = ScriptedLLM({CARD_INSTRUCTIONS: subject_if("DPDP", "Data Protection Suite for DPDP compliance")})
    await write_cards(index, collector, llm.model(), limit=1)
    assert index.card(1) == "Data Protection Suite for DPDP compliance"


async def test_the_same_rfp_uploaded_under_several_labels_counts_once_and_at_most_three_files(tmp_path):
    index, cache = setup(tmp_path, n=1)
    same = ["RFP for selection of a vendor for card rewards management " * 3] * 50
    others = [(f"other{i}.pdf", [f"distinct document number {i} " * 10] * (10 - i)) for i in range(5)]
    collector = FullCollector(cache, [HEADER], [("a.pdf", same), ("b.pdf", same), *others])
    llm = ScriptedLLM({CARD_INSTRUCTIONS: [{"subject": "", "terms": []}]})
    await write_cards(index, collector, llm.model(), limit=1)
    prompt = llm.calls[-1][1]
    assert prompt.count("=== Attachment") == 3 and prompt.count("card rewards management") >= 1
    assert "=== Attachment a.pdf" in prompt and "=== Attachment b.pdf" not in prompt  # identical content: read once
    assert "also uploaded as: b.pdf" in prompt


async def test_no_subject_anywhere_marks_the_bid_tried_without_a_card(tmp_path):
    index, cache = setup(tmp_path, n=1)
    llm = ScriptedLLM({CARD_INSTRUCTIONS: [{"subject": "", "terms": []}]})
    await write_cards(index, PartialCollector(cache, [HEADER]), llm.model(), limit=1)
    assert index.card(1) == "" and index.needing_cards() == []


async def test_a_card_that_restates_the_title_is_not_stored(tmp_path):
    index, cache = setup(tmp_path, n=1)
    llm = ScriptedLLM({CARD_INSTRUCTIONS: [{"subject": "Hiring of Agency for IT Projects- Milestone basis",
                                            "terms": ["IT projects", "milestone basis", "agency hiring"]}]})
    await write_cards(index, PartialCollector(cache, [HEADER]), llm.model(), limit=1)
    assert index.card(1) == ""


def test_title_restating_cards_from_the_first_backfill_are_cleared():
    from gem_agent.index.cards import clear_generic_cards

    index = BidIndex(":memory:")
    at = datetime(2026, 11, 10)
    index.upsert([Bid.from_listing(row(i, at, datetime(2026, 11, 28), VAGUE)) for i in (1, 2)], seen_at=at)
    index.set_card(1, "Hiring of Agency for IT Projects- Milestone basis; IT projects; milestone basis; agency hiring")
    index.set_card(2, "MSME Digital Lending Suite; digital lending")
    assert clear_generic_cards(index) == 1
    assert [b.b_id for b in index.needing_cards()] == [1] and index.card(2).startswith("MSME")


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


AML_SCOPE = ("3.2 Scope of Work\nThe Bank intends to on-board a Service Provider for Comprehensive Financial Risk and "
             "Compliance Solutions.\nIn MPGB, presently 1320 Branches are handling AML operations in Maker-Checker mode.")


async def test_the_rfp_scope_is_read_even_when_the_bid_document_names_a_subject(tmp_path):
    # 10142481 (16 Nov): the bid PDF said "Comprehensive Financial Risk and Compliance Solutions"; only the RFP's
    # scope of work said AML, deep in a 379-page RFP whose contents list also names "Scope of Work ..... 15"
    index, cache = setup(tmp_path, n=1)
    bid_doc = [HEADER, "RFP for Comprehensive Financial Risk and Compliance Solutions"]
    rfp = ["Disclaimer " * 50] * 6 + ["Contents\nScope of Work ................................ 15"] + \
          ["General terms " * 80] * 7 + [AML_SCOPE] + ["Annexure " * 80] * 300
    collector = FullCollector(cache, bid_doc, [("AMLEFRMRFP_MPGB.pdf", rfp)])
    llm = ScriptedLLM({CARD_INSTRUCTIONS: lambda p: {
        "subject": "Comprehensive Financial Risk and Compliance Solutions",
        "terms": ["anti-money laundering (AML)", "IT projects"] if "AML operations" in p else []}})
    await write_cards(index, collector, llm.model(), limit=1)
    assert "(p.15) 3.2 Scope of Work" in llm.calls[0][1] and len(llm.calls) == 1
    assert index.card(1) == "Comprehensive Financial Risk and Compliance Solutions; anti-money laundering (AML)"


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


async def test_up_to_eight_terms_are_kept_without_category_words(tmp_path):
    index, cache = setup(tmp_path, n=1)
    terms = ["AML", "transaction monitoring", "EFRM", "mule account detection", "early warning system", "KYC",
             "case management", "IT projects"]
    llm = ScriptedLLM({CARD_INSTRUCTIONS: [{"subject": "Financial risk and compliance suite", "terms": terms}]})
    await write_cards(index, PartialCollector(cache, [HEADER]), llm.model(), limit=1)
    assert index.card(1) == "Financial risk and compliance suite; " + "; ".join(terms[:7])


async def test_identical_uploads_list_every_file_name(tmp_path):
    # the same RFP was uploaded 8 times; only one copy's name ("AMLEFRMRFP_...") said what it was
    index, cache = setup(tmp_path, n=1)
    same = ["RFP for financial risk and compliance " * 3] * 20
    collector = FullCollector(cache, [HEADER], [("1794046391.pdf", same), ("AMLEFRMRFP_MPGB.pdf", same)])
    llm = ScriptedLLM({CARD_INSTRUCTIONS: [{"subject": "", "terms": []}]})
    await write_cards(index, collector, llm.model(), limit=1)
    assert llm.calls[0][1].count("=== Attachment") == 1 and "AMLEFRMRFP_MPGB.pdf" in llm.calls[0][1]


def test_redo_clears_every_card_including_tried_ones():
    index = BidIndex(":memory:")
    at = datetime(2026, 11, 10)
    index.upsert([Bid.from_listing(row(i, at, datetime(2026, 11, 28), VAGUE)) for i in (1, 2, 3)], seen_at=at)
    index.set_card(1, "MSME Digital Lending Suite")
    index.set_card(2, "")
    assert index.clear_cards() == 2
    assert [b.b_id for b in index.needing_cards()] == [1, 2, 3] 
    assert "Lending" not in index._conn.execute("SELECT embed_text FROM bids WHERE b_id = 1").fetchone()[0]
