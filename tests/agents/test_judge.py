from datetime import datetime

import pytest

from gem_agent.agents.judge import (
    JUDGE_INSTRUCTIONS,
    NOTES_INSTRUCTIONS,
    TRIAGE_INSTRUCTIONS,
    Judge,
    JudgeSettings,
)
from gem_agent.cache.docs import DocCache
from gem_agent.cache.verdicts import VerdictCache
from gem_agent.tools.gem.models import Bid
from tests.agents.helpers import LOS, ScriptedLLM, bid_files

BID = Bid(b_id=1, bid_number="GEM/2026/B/1", title="Custom Bid for Services - LOS implementation",
          ministry="Ministry of Finance", department="Department of Financial Services", bid_type=1)
SCOPE = "Scope of work: the bidder shall implement a Loan Origination System for the bank's retail loans"
GOOD_QUOTE = {"quote": "the bidder shall implement a Loan Origination System for the bank's retail loans",
              "file": "F2", "page": 2}
FACTS = {"emd": "Rs 5,00,000", "min_turnover": "50 crore", "contract_period": "5 years", "estimated_value": None,
         "scope_summary": "LOS implementation"}


def yes(confidence=0.9, quotes=(GOOD_QUOTE,)):
    return {"relevant": True, "confidence": confidence, "reason": "The RFP is for a Loan Origination System.",
            "evidence": list(quotes), "key_facts": FACTS, "queries": NO_QUERIES}


NO_QUERIES = {"kind": "none", "quote": None, "year": None, "month": None, "day": None, "hour": None, "minute": None}
TRIAGE_MAYBE = {"decision": "maybe", "reason": "title mentions LOS"}


@pytest.fixture
def cache(tmp_path):
    return DocCache(tmp_path)


@pytest.fixture
def files(cache):
    return bid_files(cache, 1, [
        ("bid.pdf", "bid_document", ["Bid Number GEM/2026/B/1 Ministry of Finance item Custom Bid for Services",
                                     "Buyer added terms: see RFP"]),
        ("rfp.pdf", "attachment", ["Introduction to the bank and its branches", SCOPE,
                                   "Eligibility: average turnover of 50 crore", "EMD Rs 5,00,000"]),
        ("gtc.pdf", "gtc", ["General terms and conditions applicable to all bids"]),
    ])


def make_judge(llm, cache, **settings):
    return Judge(llm.model(), cache, VerdictCache(cache.root), settings=JudgeSettings(**settings))


async def test_clear_no_at_triage_stops_before_the_judge(cache, files):
    llm = ScriptedLLM({TRIAGE_INSTRUCTIONS: [{"decision": "clearly_no", "reason": "lockers, not software"}]})
    verdict = await make_judge(llm, cache).judge(LOS, BID, files)
    assert not verdict.relevant and verdict.stage == "triage" and verdict.triage == "clearly_no"
    assert llm.count(JUDGE_INSTRUCTIONS) == 0


async def test_relevant_verdict_carries_verified_evidence_and_facts(cache, files):
    llm = ScriptedLLM({TRIAGE_INSTRUCTIONS: [TRIAGE_MAYBE], JUDGE_INSTRUCTIONS: [yes()]})
    verdict = await make_judge(llm, cache).judge(LOS, BID, files)
    assert verdict.relevant and verdict.stage == "judge" and not verdict.needs_review
    ev = verdict.evidence[0]
    assert (ev.file, ev.page, ev.verified) == ("rfp.pdf", 2, True)
    assert verdict.key_facts.emd == "Rs 5,00,000"


async def test_quote_not_in_the_documents_is_sent_back_once(cache, files):
    invented = {"quote": "the bank needs an anti money laundering system urgently", "file": "F2", "page": 2}
    llm = ScriptedLLM({TRIAGE_INSTRUCTIONS: [TRIAGE_MAYBE], JUDGE_INSTRUCTIONS: [yes(quotes=[invented]), yes()]})
    verdict = await make_judge(llm, cache).judge(LOS, BID, files)
    assert llm.count(JUDGE_INSTRUCTIONS) == 2
    assert "could not be found" in llm.calls[-1][1]
    assert verdict.evidence[0].verified and not verdict.needs_review


async def test_a_real_quote_cited_on_the_wrong_page_is_found_and_its_page_corrected(cache, files):
    # 8253403 (16 Nov): a docx is one page, sent as windows marked p.1; the model cited p.3 and p.2
    off_page = {**GOOD_QUOTE, "page": 4}
    llm = ScriptedLLM({TRIAGE_INSTRUCTIONS: [TRIAGE_MAYBE], JUDGE_INSTRUCTIONS: [yes(quotes=[off_page])]})
    verdict = await make_judge(llm, cache).judge(LOS, BID, files)
    ev = verdict.evidence[0]
    assert (ev.file, ev.page, ev.verified) == ("rfp.pdf", 2, True) and not verdict.needs_review
    assert llm.count(JUDGE_INSTRUCTIONS) == 1  # nothing to send back


async def test_a_quote_citing_the_whole_passage_header_is_checked_against_that_file(cache, files):
    # 8255061 (16 Nov): the model wrote "[F3 p.1 | 1794202410.pdf]" as the file; three real quotes failed
    header = {**GOOD_QUOTE, "file": "[F2 p.2 | rfp.pdf]"}
    by_name = {**GOOD_QUOTE, "file": "rfp.pdf"}
    llm = ScriptedLLM({TRIAGE_INSTRUCTIONS: [TRIAGE_MAYBE], JUDGE_INSTRUCTIONS: [yes(quotes=[header, by_name])]})
    verdict = await make_judge(llm, cache).judge(LOS, BID, files)
    assert [(e.file, e.verified) for e in verdict.evidence] == [("rfp.pdf", True), ("rfp.pdf", True)]
    assert not verdict.needs_review


async def test_unverifiable_relevant_verdict_needs_review(cache, files):
    invented = {"quote": "the bank needs an anti money laundering system urgently", "file": "F2", "page": 2}
    llm = ScriptedLLM({TRIAGE_INSTRUCTIONS: [TRIAGE_MAYBE], JUDGE_INSTRUCTIONS: [yes(quotes=[invented])]})
    verdict = await make_judge(llm, cache).judge(LOS, BID, files)
    assert verdict.relevant and verdict.needs_review and verdict.confidence <= 0.5
    assert not verdict.evidence[0].verified


async def test_not_relevant_is_not_sent_back(cache, files):
    no = {"relevant": False, "confidence": 0.9, "reason": "Core banking only.",
          "evidence": [{"quote": "Introduction to the bank and its branches", "file": "F2", "page": 1}], "key_facts": FACTS,
          "queries": NO_QUERIES}
    llm = ScriptedLLM({TRIAGE_INSTRUCTIONS: [TRIAGE_MAYBE], JUDGE_INSTRUCTIONS: [no]})
    verdict = await make_judge(llm, cache).judge(LOS, BID, files)
    assert not verdict.relevant and llm.count(JUDGE_INSTRUCTIONS) == 1 and not verdict.needs_review


async def test_borderline_verdict_reads_the_rfp_in_steps(cache):
    pages = [f"Page {i} of the RFP about general banking operations and branch network" for i in range(1, 46)]
    pages[30] = SCOPE
    files = bid_files(cache, 2, [("bid.pdf", "bid_document", ["Bid GEM/2026/B/2 for services of a bank"]),
                                 ("rfp.pdf", "attachment", pages)])
    bid = BID.model_copy(update={"b_id": 2, "bid_number": "GEM/2026/B/2"})
    quote = {**GOOD_QUOTE, "page": 31}
    llm = ScriptedLLM({
        TRIAGE_INSTRUCTIONS: [TRIAGE_MAYBE],
        NOTES_INSTRUCTIONS: lambda p: {"notes": "Page 31 asks for an LOS." if "Loan Origination" in p else "",
                                       "pages": [31] if "Loan Origination" in p else []},
        JUDGE_INSTRUCTIONS: [yes(confidence=0.4, quotes=[quote]), yes(confidence=0.85, quotes=[quote])],
    })
    verdict = await make_judge(llm, cache, deep_pages_per_call=20).judge(LOS, bid, files)
    assert llm.count(NOTES_INSTRUCTIONS) == 3  # 45 pages in steps of 20
    assert verdict.stage == "deep" and verdict.confidence == 0.85
    assert "Page 31 asks for an LOS." in llm.calls[-1][1]


async def test_same_question_is_never_judged_twice(cache, files):
    llm = ScriptedLLM({TRIAGE_INSTRUCTIONS: [TRIAGE_MAYBE], JUDGE_INSTRUCTIONS: [yes()]})
    judge = make_judge(llm, cache)
    first = await judge.judge(LOS, BID, files)
    calls = len(llm.calls)
    assert await judge.judge(LOS, BID, files) == first and len(llm.calls) == calls
    other = LOS.model_copy(update={"meaning": "Line of Sight radio survey"})
    await judge.judge(other, BID, files)
    assert len(llm.calls) > calls


async def test_cached_verdict_survives_restart(cache, files):
    llm = ScriptedLLM({TRIAGE_INSTRUCTIONS: [TRIAGE_MAYBE], JUDGE_INSTRUCTIONS: [yes()]})
    first = await make_judge(llm, cache).judge(LOS, BID, files)
    again = await make_judge(ScriptedLLM({}), cache).judge(LOS, BID, files)
    assert again == first


async def test_prompts_put_fixed_text_and_topic_before_the_bid(cache, files):
    llm = ScriptedLLM({TRIAGE_INSTRUCTIONS: [TRIAGE_MAYBE], JUDGE_INSTRUCTIONS: [yes()]})
    await make_judge(llm, cache).judge(LOS, BID, files)
    triage_prompt, judge_prompt = llm.calls[0][1], llm.calls[1][1]
    for prompt in (triage_prompt, judge_prompt):
        assert prompt.startswith(LOS.render())
        assert "NOT this (look-alikes): LOST (lost/found)" in prompt
    assert "Custom Bid for Services - LOS implementation" in triage_prompt
    assert "[F2 p.2 | rfp.pdf]" in judge_prompt and SCOPE in judge_prompt
    assert "General terms and conditions" not in judge_prompt


async def test_judge_prompt_stays_within_budget(cache):
    big = ["Loan Origination System requirement " + "detail " * 600 for _ in range(60)]
    files = bid_files(cache, 3, [("bid.pdf", "bid_document", ["Bid GEM/2026/B/3 " + "x " * 3000]),
                                 ("rfp.pdf", "attachment", big)])
    llm = ScriptedLLM({TRIAGE_INSTRUCTIONS: [TRIAGE_MAYBE],
                       JUDGE_INSTRUCTIONS: [yes(quotes=[{**GOOD_QUOTE, "quote": "Loan Origination System requirement detail detail"}])]})
    await make_judge(llm, cache, context_chars=20000).judge(LOS, BID.model_copy(update={"b_id": 3}), files)
    assert len(llm.calls[0][1]) <= 6000 + len(LOS.render()) + 600
    assert len(llm.calls[1][1]) <= 20000 + len(LOS.render()) + 1500


def test_schema_requires_evidence_and_facts():
    from gem_agent.agents.schemas import JudgeOutput

    schema = JudgeOutput.model_json_schema()
    assert {"evidence", "key_facts"} <= set(schema["required"])
    assert schema["properties"]["evidence"]["minItems"] == 1


def test_every_model_facing_schema_requires_all_its_fields():
    from gem_agent.agents.chat import ChooseOutput, UnderstandOutput
    from gem_agent.agents.memory_step import MemoryProposal
    from gem_agent.agents.planner import plan_output_model
    from gem_agent.agents.schemas import (
        JudgeOutput,
        KeyFacts,
        MemoryChange,
        NotesOutput,
        QueriesOut,
        TriageOutput,
    )

    models = [ChooseOutput, UnderstandOutput, MemoryProposal, JudgeOutput, KeyFacts, QueriesOut, MemoryChange, NotesOutput,
              TriageOutput, plan_output_model(["m"], ["d"], ["c"])]
    for model in models:
        schema = model.model_json_schema()
        assert set(schema.get("required", [])) == set(schema["properties"]), model.__name__


def test_free_text_in_model_answers_is_bounded():
    # 16 Nov live run: the title screen's free-text reason ran past max_tokens and the JSON was cut off
    from gem_agent.agents.chat import ChooseOutput, TopicOut
    from gem_agent.agents.memory_step import MemoryProposal
    from gem_agent.agents.schemas import (
        JudgeOutput,
        KeyFacts,
        MemoryChange,
        NotesOutput,
        QuoteOut,
        TriageOutput,
    )
    from gem_agent.agents.searcher import ScreenOutput

    def strings(schema, defs, path=""):
        for name, prop in schema.get("properties", {}).items():
            for p in [prop, *prop.get("anyOf", [])]:
                if "$ref" in p:
                    yield from strings(defs[p["$ref"].split("/")[-1]], defs, f"{path}{name}.")
                elif p.get("type") == "string":
                    yield f"{path}{name}", p
                elif p.get("type") == "array" and p.get("items", {}).get("type") == "string":
                    yield f"{path}{name}[]", p["items"]

    for model in (ChooseOutput, TopicOut, MemoryProposal, JudgeOutput, KeyFacts, MemoryChange, NotesOutput, QuoteOut,
                  TriageOutput, ScreenOutput):
        schema = model.model_json_schema()
        for path, prop in strings(schema, schema.get("$defs", {})):
            assert "maxLength" in prop or "enum" in prop, f"{model.__name__}.{path}"
    assert "reason" not in ScreenOutput.model_json_schema()["properties"]


async def test_screen_then_judge_makes_one_triage_call(cache, files):
    llm = ScriptedLLM({TRIAGE_INSTRUCTIONS: [TRIAGE_MAYBE], JUDGE_INSTRUCTIONS: [yes()]})
    judge = make_judge(llm, cache)
    rejected, triage = await judge.screen(LOS, BID, files)
    assert rejected is None and triage.decision == "maybe"
    verdict = await judge.judge(LOS, BID, files, triage=triage)
    assert verdict.relevant and llm.count(TRIAGE_INSTRUCTIONS) == 1


async def test_screen_rejects_and_stores_a_clear_no(cache, files):
    llm = ScriptedLLM({TRIAGE_INSTRUCTIONS: [{"decision": "clearly_no", "reason": "lockers"}]})
    judge = make_judge(llm, cache)
    rejected, _ = await judge.screen(LOS, BID, files)
    assert rejected is not None and rejected.stage == "triage" and not rejected.relevant
    assert await judge.judge(LOS, BID, files) == rejected  # cached


# --- evidence must come from the documents, not from the tender's own title ------------------------------------


# the tender's own title, as the bid PDF repeats it on page 1
BID_QUOTE = {"quote": "Ministry of Finance item Custom Bid for Services", "file": "F1", "page": 1}


async def test_a_title_quote_alone_is_sent_back_for_an_attachment_quote(cache, files):
    # 16 Nov: a relevant verdict was accepted on a quote of the tender's own title from the bid PDF
    verdicts = [yes(quotes=[BID_QUOTE]), yes()]
    llm = ScriptedLLM({TRIAGE_INSTRUCTIONS: [TRIAGE_MAYBE], JUDGE_INSTRUCTIONS: verdicts})
    verdict = await make_judge(llm, cache).judge(LOS, BID, files)
    assert llm.count(JUDGE_INSTRUCTIONS) == 2
    assert "bid document" in llm.calls[-1][1] and "attachment" in llm.calls[-1][1]
    assert verdict.evidence[0].file == "rfp.pdf" and not verdict.needs_review


async def test_still_title_only_after_the_retry_needs_review(cache, files):
    llm = ScriptedLLM({TRIAGE_INSTRUCTIONS: [TRIAGE_MAYBE], JUDGE_INSTRUCTIONS: [yes(quotes=[BID_QUOTE])]})
    verdict = await make_judge(llm, cache).judge(LOS, BID, files)
    assert verdict.relevant and verdict.needs_review and verdict.confidence <= 0.5
    assert verdict.evidence[0].verified  # the quote is real, just from the wrong document


async def test_an_attachment_quote_needs_no_retry(cache, files):
    llm = ScriptedLLM({TRIAGE_INSTRUCTIONS: [TRIAGE_MAYBE], JUDGE_INSTRUCTIONS: [yes()]})
    await make_judge(llm, cache).judge(LOS, BID, files)
    assert llm.count(JUDGE_INSTRUCTIONS) == 1


async def test_a_quote_from_the_buyer_added_terms_in_the_bid_document_counts(cache):
    # only pages 1-2 of the bid PDF repeat the title; the buyer-added terms (page 4-5) say what is bought, and a
    # bid can have no attachment at all
    files = bid_files(cache, 4, [("bid.pdf", "bid_document", [
        "Bid Number GEM/2026/B/4 Ministry of Finance item Custom Bid for Services", "Bid details and dates",
        "Technical evaluation", "Buyer Added Bid Specific Terms: implementation of a Loan Origination System for the bank"])])
    quote = {"quote": "implementation of a Loan Origination System for the bank", "file": "F1", "page": 4}
    llm = ScriptedLLM({TRIAGE_INSTRUCTIONS: [TRIAGE_MAYBE], JUDGE_INSTRUCTIONS: [yes(quotes=[quote])]})
    verdict = await make_judge(llm, cache).judge(LOS, BID, files)
    assert llm.count(JUDGE_INSTRUCTIONS) == 1
    assert verdict.relevant and not verdict.needs_review and verdict.confidence == 0.9


def no(quotes):
    return {"relevant": False, "confidence": 0.95, "reason": "A land survey, not software.", "evidence": quotes,
            "key_facts": FACTS, "queries": NO_QUERIES}


async def test_a_rejection_quoting_only_the_title_is_sent_back_once_for_a_document_quote(cache, files):
    # 8182010 (16 Nov): ruled out "95% sure" on its title and a "Buyer:" line from our own header, while its scope
    # said plainly that it is a topographical survey
    header = {"quote": "Buyer: Ministry of Finance / Department of Financial Services", "file": "F1", "page": 1}
    scope = {"quote": "Introduction to the bank and its branches", "file": "F2", "page": 1}
    llm = ScriptedLLM({TRIAGE_INSTRUCTIONS: [TRIAGE_MAYBE], JUDGE_INSTRUCTIONS: [no([BID_QUOTE, header]), no([scope])]})
    verdict = await make_judge(llm, cache).judge(LOS, BID, files)
    assert llm.count(JUDGE_INSTRUCTIONS) == 2 and "attachment" in llm.calls[-1][1]
    assert not verdict.relevant and verdict.evidence[0].file == "rfp.pdf" and not verdict.needs_review


async def test_a_rejection_still_on_its_title_after_the_retry_is_kept_unflagged(cache, files):
    llm = ScriptedLLM({TRIAGE_INSTRUCTIONS: [TRIAGE_MAYBE], JUDGE_INSTRUCTIONS: [no([BID_QUOTE])]})
    verdict = await make_judge(llm, cache).judge(LOS, BID, files)
    assert llm.count(JUDGE_INSTRUCTIONS) == 2
    assert not verdict.relevant and not verdict.needs_review and verdict.confidence == 0.95


def test_the_judge_is_told_not_to_quote_the_summary_lines():
    assert '"Buyer" lines' in JUDGE_INSTRUCTIONS and "never quote them" in JUDGE_INSTRUCTIONS


async def test_every_attachment_shows_its_opening_page_even_when_nothing_matches_the_topic(cache):
    # 8182010: no AI words anywhere, so matching picked only GeM's standard bid pages; the scope (a land survey) and
    # the terms were never shown and the rejection could only quote the title
    files = bid_files(cache, 4, [
        ("bid.pdf", "bid_document", ["Bid GEM/2026/B/4", "dates", "Eligibility requirements: turnover",
                                     "Bidder requirements: experience"]),
        ("scope.pdf", "attachment", ["Topographical survey of the station: jungle clearing, contours at 1 m"]),
        ("atc.pdf", "attachment", ["Additional terms: the survey shall be completed in 120 days"]),
    ])
    llm = ScriptedLLM({TRIAGE_INSTRUCTIONS: [TRIAGE_MAYBE], JUDGE_INSTRUCTIONS: [no([
        {"quote": "Topographical survey of the station: jungle clearing, contours at 1 m", "file": "F2", "page": 1}])]})
    verdict = await make_judge(llm, cache, scope_k=1).judge(LOS, BID.model_copy(update={"b_id": 4}), files)
    prompt = llm.calls[-1][1]
    assert "Topographical survey" in prompt and "completed in 120 days" in prompt
    assert verdict.evidence[0].verified and llm.count(JUDGE_INSTRUCTIONS) == 1


def test_proof_says_what_the_quotes_show_not_how_sure_the_model_felt():
    from gem_agent.agents.judge import proof
    from gem_agent.agents.schemas import Evidence
    roles = {"bid.pdf": "bid_document", "rfp.pdf": "attachment", "gtc.pdf": "gtc"}

    def ev(file, page, verified=True):
        return Evidence(quote="q", file=file, page=page, verified=verified)
    assert proof([ev("bid.pdf", 1), ev("rfp.pdf", 4)], roles) == "documents"
    assert proof([ev("bid.pdf", 5)], roles) == "documents"  # the buyer-added terms
    assert proof([ev("bid.pdf", 1), ev("gtc.pdf", 3)], roles) == "title"  # GeM's general terms prove nothing
    assert proof([ev("rfp.pdf", 4, verified=False), ev("bid.pdf", 2)], roles) == "title"
    assert proof([ev("rfp.pdf", 4, verified=False)], roles) == "none"
    assert proof([], roles) == "none"


async def test_a_real_quote_cited_from_the_wrong_file_is_found_and_its_file_corrected(cache, files):
    # 8266583 (17 Nov): the bid document's "Similar Category" line was cited as the Scope of Work and shown "not found"
    wrong_file = {"quote": "Ministry of Finance item Custom Bid for Services", "file": "F2", "page": 1}
    llm = ScriptedLLM({TRIAGE_INSTRUCTIONS: [TRIAGE_MAYBE], JUDGE_INSTRUCTIONS: [yes(quotes=[GOOD_QUOTE, wrong_file])]})
    verdict = await make_judge(llm, cache).judge(LOS, BID, files)
    moved = verdict.evidence[1]
    assert moved.verified and moved.file == "bid.pdf" and moved.page == 1


async def test_a_quote_found_nowhere_stays_unverified(cache, files):
    invented = {"quote": "the bank needs an anti money laundering system urgently", "file": "F2", "page": 2}
    llm = ScriptedLLM({TRIAGE_INSTRUCTIONS: [TRIAGE_MAYBE], JUDGE_INSTRUCTIONS: [yes(quotes=[GOOD_QUOTE, invented])]})
    verdict = await make_judge(llm, cache).judge(LOS, BID, files)
    assert not verdict.evidence[1].verified and verdict.evidence[1].file == "rfp.pdf"


def test_the_judge_is_told_future_work_and_gems_similar_categories_are_not_in_scope():
    assert "future extension" in JUDGE_INSTRUCTIONS and '"Similar Category"' in JUDGE_INSTRUCTIONS
    assert '"Similar Category"' in TRIAGE_INSTRUCTIONS


# --- the last date for queries (17 Nov: 8272331's RFP cover page said it; GeM's listing has no such field) -------

COVER = ("Date of Publishing Tender : 13th November 2026 Last Date of Tender submission : 7th December 2026, 04.00 PM "
         "Last date for submission of Queries : 20th November 2026, 05.00 PM Technical bid Opening : 8th December 2026")


def rfp_files(cache, bid_pages):
    return bid_files(cache, 3, [("bid.pdf", "bid_document", bid_pages),
                                ("rfp.pdf", "attachment", [COVER, *["General terms of the RFP"] * 12, SCOPE])])


async def test_the_queries_date_from_the_rfp_is_kept_with_a_checked_quote(cache):
    files = rfp_files(cache, ["Bid Number GEM/2026/B/3 Custom Bid for Services", "Buyer added terms: see RFP"])
    quote = {**GOOD_QUOTE, "page": 14}
    queries = {"kind": "queries_deadline",
               "quote": "Last date for submission of Queries : 20th November 2026, 05.00 PM",
               "year": 2026, "month": 11, "day": 20, "hour": 17, "minute": 0}
    llm = ScriptedLLM({TRIAGE_INSTRUCTIONS: [TRIAGE_MAYBE], JUDGE_INSTRUCTIONS: [{**yes(quotes=[quote]), "queries": queries}]})
    verdict = await make_judge(llm, cache).judge(LOS, BID.model_copy(update={"b_id": 3}), files)
    q = verdict.queries
    assert q and q.when == datetime(2026, 11, 20, 17, 0) and q.source == "documents"
    assert (q.file, q.page, q.verified) == ("rfp.pdf", 1, True)
    assert "submission of Queries" in llm.calls[-1][1]  # the cover page reached the Judge though it never names LOS


async def test_a_queries_quote_not_in_the_documents_is_kept_unverified(cache):
    files = rfp_files(cache, ["Bid Number GEM/2026/B/3", "Buyer added terms"])
    queries = {"kind": "queries_deadline", "quote": "Queries must reach us by 21 November 2026", "year": 2026, "month": 11, "day": 21,
               "hour": None, "minute": None}
    llm = ScriptedLLM({TRIAGE_INSTRUCTIONS: [TRIAGE_MAYBE], JUDGE_INSTRUCTIONS: [{**yes(), "queries": queries}]})
    verdict = await make_judge(llm, cache).judge(LOS, BID.model_copy(update={"b_id": 3}), files)
    assert verdict.queries and not verdict.queries.verified


async def test_gems_own_pre_bid_date_wins_and_needs_no_model(cache):
    block = "Pre Bid Detail(s)\nPre-Bid Date and Time\nPre-Bid Venue\n18-11-2026 11:00:00\nOnline"
    files = rfp_files(cache, ["Bid Number GEM/2026/B/3", block])
    llm = ScriptedLLM({TRIAGE_INSTRUCTIONS: [TRIAGE_MAYBE], JUDGE_INSTRUCTIONS: [yes(quotes=[{**GOOD_QUOTE, "page": 14}])]})
    verdict = await make_judge(llm, cache).judge(LOS, BID.model_copy(update={"b_id": 3}), files)
    q = verdict.queries
    assert q and q.when == datetime(2026, 11, 18, 11, 0) and q.source == "gem_pre_bid" and q.verified
    assert q.kind == "pre_bid_meeting"
    assert (q.file, q.page) == ("bid.pdf", 2)


async def test_a_triage_rejection_still_carries_gems_pre_bid_date(cache):
    block = "Pre Bid Detail(s)\nPre-Bid Date and Time\nPre-Bid Venue\n18-11-2026 11:00:00\nOnline"
    files = rfp_files(cache, ["Bid Number GEM/2026/B/3", block])
    llm = ScriptedLLM({TRIAGE_INSTRUCTIONS: [{"decision": "clearly_no", "reason": "lockers"}]})
    verdict = await make_judge(llm, cache).judge(LOS, BID.model_copy(update={"b_id": 3}), files)
    assert verdict.queries and verdict.queries.source == "gem_pre_bid"


def test_an_incomplete_or_impossible_queries_date_is_dropped():
    from gem_agent.agents.judge import queries_date
    from gem_agent.agents.schemas import JudgeOutput, QueriesOut

    assert "queries" in JudgeOutput.model_json_schema()["required"]
    q = lambda **kw: QueriesOut(**{**NO_QUERIES, "quote": "x", **kw})
    assert queries_date(q(year=2026, month=11, day=20, hour=17, minute=0)) == datetime(2026, 11, 20, 17, 0)
    assert queries_date(q(year=2026, month=11, day=20)) == datetime(2026, 11, 20, 23, 59)  # a day counts to its end
    assert queries_date(q(year=2026, month=2, day=30)) is None and queries_date(q(month=11, day=20)) is None


async def test_the_rfp_cover_page_reaches_the_judge_when_a_queries_clause_outranks_it(cache):
    # 8272331 (17 Nov): page 27's "17. QUERIES" clause says "queries" four times and outranked the cover page, which
    # states the date once; the same 66-page RFP was uploaded twice, so its copy took the second slot
    clause = "17. QUERIES 17.1 The queries, if any, can be sent by email. Queries format in Annexure. No queries by phone."
    rfp = [COVER, *[f"Section {i} general terms of the RFP" for i in range(2, 27)], clause, SCOPE]
    files = bid_files(cache, 5, [("bid.pdf", "bid_document", ["Bid Number GEM/2026/B/5", "Buyer added terms"]),
                                 ("rfp.pdf", "attachment", rfp), ("rfp copy.pdf", "attachment", rfp)])
    llm = ScriptedLLM({TRIAGE_INSTRUCTIONS: [TRIAGE_MAYBE], JUDGE_INSTRUCTIONS: [yes(quotes=[{**GOOD_QUOTE, "page": 28}])]})
    await make_judge(llm, cache).judge(LOS, BID.model_copy(update={"b_id": 5}), files)
    prompt = llm.calls[-1][1]
    assert "submission of Queries" in prompt
    assert prompt.count("17.1 The queries") == 1  # the copy's identical page is not shown twice


def test_a_queries_date_on_or_after_closing_is_not_a_queries_date():
    # 8253937 (17 Nov): the Judge gave the closing date, 30 Nov 17:00, as the queries date
    from gem_agent.agents.judge import plausible_queries
    from gem_agent.agents.schemas import QueriesDeadline

    q = lambda when: QueriesDeadline(when=when, source="documents", quote="x", file="f", page=1, verified=False)
    closing = datetime(2026, 11, 30, 17, 0)
    assert plausible_queries(q(datetime(2026, 11, 20, 17, 0)), closing)
    assert plausible_queries(q(closing), closing) is None and plausible_queries(None, closing) is None
    assert plausible_queries(q(closing), None)  # no closing date known: kept
    # 8274258 (17 Nov): "28 Nov 2025" on a bid published in Nov 2026
    published = datetime(2026, 11, 14, 10, 0)
    assert plausible_queries(q(datetime(2025, 11, 28, 17, 0)), closing, published) is None
    assert plausible_queries(q(datetime(2026, 11, 20, 17, 0)), closing, published)



@pytest.mark.parametrize("answer", [
    # 8229156, 8246802 (17 Nov): the bid's own end date and the tender submission date, given as the queries date
    {"kind": "bid_submission", "quote": "Bid End Date/Time 16-11-2026 11:00:00", "day": 16, "hour": 11},
    # 8263340, 8259765: a sentence about queries with no date in it, and a date from elsewhere
    {"kind": "queries_deadline", "quote": "all the queries are communicated on or before the date given", "day": 13},
])
def test_a_queries_answer_is_dropped_when_it_is_another_date_or_its_words_hold_no_date(answer):
    from gem_agent.agents.schemas import QueriesOut

    out = QueriesOut(**{**NO_QUERIES, "year": 2026, "month": 11, "minute": 0, **answer})
    assert Judge._queries(out, [], {}) is None


def test_a_pre_bid_meeting_date_counts_and_a_two_digit_year_is_read():
    from gem_agent.agents.schemas import QueriesOut

    out = QueriesOut(**{**NO_QUERIES, "kind": "pre_bid_meeting", "quote": "Pre Bid 13.11.26 at 11:00 AM",
                        "year": 2026, "month": 11, "day": 13, "hour": 11, "minute": 0})
    q = Judge._queries(out, [], {})
    assert q.when == datetime(2026, 11, 13, 11, 0) and q.kind == "pre_bid_meeting"



async def test_gems_pre_bid_block_split_across_a_page_break_is_read(cache):
    # 8179594 (17 Nov): the heading ends page 5 of the bid document, the date starts page 6
    files = rfp_files(cache, ["Bid Number GEM/2026/B/3", "terms ... Pre Bid Detail(s)\nPre-Bid Date and Time",
                              "Pre-Bid Venue\n02-11-2026 15:00:00\nVendor may forward their queries"])
    llm = ScriptedLLM({TRIAGE_INSTRUCTIONS: [{"decision": "clearly_no", "reason": "lockers"}]})
    verdict = await make_judge(llm, cache).judge(LOS, BID.model_copy(update={"b_id": 3}), files)
    q = verdict.queries
    assert q and q.source == "gem_pre_bid" and q.when == datetime(2026, 11, 2, 15, 0) and q.page == 3
