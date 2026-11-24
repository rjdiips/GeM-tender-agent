from datetime import date, datetime

import pytest

from gem_agent.agents.chat import (
    CHOOSE_INSTRUCTIONS,
    UNDERSTAND_INSTRUCTIONS,
    ChatAgent,
    mentions_dates,
    says_only,
)
from gem_agent.agents.schemas import ProbeResult, TenderRequest
from gem_agent.memory.topics import TopicStore
from gem_agent.memory.user_memory import MemoryStore
from tests.agents.helpers import LOS, ScriptedLLM

NOW = datetime(2026, 11, 16, 13, 40)
SIGHT = LOS.model_copy(update={"meaning": "Line of Sight: radio survey for telecom towers", "domain": "telecom"})


def topic_out(t):
    name, _, description = t.meaning.partition(": ")
    return {"name": name, "description": description or name, "domain": t.domain, "match_terms": t.match_terms,
            "acronyms": [{"acronym": k, "expansion": v} for k, v in t.acronyms.items()],
            "related_but_not_enough": t.related_but_not_enough, "not_this": t.not_this}


def candidates(*topics, **extra):
    return {"intent": "search", "candidates": [{"topic": topic_out(t), "plausibility": p} for t, p in topics],
            "window": {"kind": "published", "last_days": 14, "next_days": None, "from_date": None, "to_date": None},
            "buyer_hints": [], "buyer_only": False, "stated_facts": [], **extra}


class FakeProbe:
    def __init__(self, counts):
        self.counts, self.calls = counts, []

    async def __call__(self, topic, window):
        self.calls.append(topic.meaning)
        strong = self.counts.get(topic.meaning, 0)
        return ProbeResult(meaning=topic.meaning, window_count=36104, strong=strong,
                           top_titles=[f"{topic.domain} title {i}" for i in range(3)])


@pytest.fixture
def stores(tmp_path):
    return MemoryStore(tmp_path), TopicStore(tmp_path)


def agent(llm, probe, stores):
    memory, topics = stores
    return ChatAgent(llm.model(), probe=probe, memory=memory, topics=topics, now=lambda: NOW)


async def test_single_meaning_needs_one_call(stores):
    llm = ScriptedLLM({UNDERSTAND_INSTRUCTIONS: [candidates((LOS, 0.9))]})
    probe = FakeProbe({LOS.meaning: 48})
    result = await agent(llm, probe, stores).understand("asha", "LOS tenders published in the last 2 weeks", [], run_id="r1")
    assert [t.meaning for t in result.request.topics] == [LOS.meaning]
    assert llm.count(CHOOSE_INSTRUCTIONS) == 0
    assert result.request.window.published_from == datetime(2026, 11, 2)
    assert result.progress_line == ("Looking for Loan Origination System tenders published 2–16 Nov "
                                    "(36,104 open bids in that window; 48 look relevant from their titles).")


async def test_prompt_carries_today_memory_and_saved_meanings(stores):
    memory, topics = stores
    memory.add_stated("asha", "We sell AML and LOS software to banks", run_id="r0", today=date(2026, 11, 10))
    topics.save("asha", "LOS tenders", [LOS])
    llm = ScriptedLLM({UNDERSTAND_INSTRUCTIONS: [candidates((LOS, 0.9))]})
    await agent(llm, FakeProbe({}), stores).understand("asha", "LOS tenders", ["earlier: AML tenders"], run_id="r1")
    prompt = llm.calls[0][1]
    assert "Today is Monday 16 November 2026" in prompt
    assert "We sell AML and LOS software to banks" in prompt
    assert "los tenders → Loan Origination System" in prompt
    assert "earlier: AML tenders" in prompt


async def test_two_plausible_meanings_are_chosen_with_site_evidence(stores):
    llm = ScriptedLLM({UNDERSTAND_INSTRUCTIONS: [candidates((LOS, 0.6), (SIGHT, 0.4))],
                       CHOOSE_INSTRUCTIONS: [{"keep": [0, 1], "chosen_because": "both have open tenders"}]})
    probe = FakeProbe({LOS.meaning: 4, SIGHT.meaning: 2})
    result = await agent(llm, probe, stores).understand("asha", "LOS tenders", [], run_id="r1")
    assert probe.calls == [LOS.meaning, SIGHT.meaning]
    assert "4 look relevant" in llm.calls[1][1] and "telecom title 0" in llm.calls[1][1]
    assert [t.meaning for t in result.request.topics] == [LOS.meaning, SIGHT.meaning]
    assert result.request.topics[0].other_meanings_searched == [SIGHT.meaning]
    assert result.progress_line == ("Searching two meanings: Loan Origination System (4) · Line of Sight (2), "
                                    "published 2–16 Nov; results grouped, likeliest first.")
    _, topics = stores
    assert [t.meaning for t in topics.get("asha", "LOS tenders")] == [LOS.meaning, SIGHT.meaning]  # both saved


async def test_choice_can_drop_a_meaning(stores):
    llm = ScriptedLLM({UNDERSTAND_INSTRUCTIONS: [candidates((LOS, 0.6), (SIGHT, 0.4))],
                       CHOOSE_INSTRUCTIONS: [{"keep": [0], "chosen_because": "no open Line of Sight tenders"}]})
    result = await agent(llm, FakeProbe({LOS.meaning: 4}), stores).understand("asha", "LOS", [], run_id="r1")
    assert len(result.request.topics) == 1
    assert result.request.topics[0].chosen_because == "no open Line of Sight tenders"


async def test_stated_facts_are_saved_at_once(stores):
    memory, _ = stores
    llm = ScriptedLLM({UNDERSTAND_INSTRUCTIONS: [candidates((LOS, 0.9), stated_facts=["We're a banking software company"])]})
    await agent(llm, FakeProbe({}), stores).understand("asha", "we're a banking software company; LOS tenders", [],
                                                          run_id="r1")
    assert [f.fact for f in memory.prompt_facts("asha")] == ["We're a banking software company"]


async def test_non_search_intent_passes_through(stores):
    llm = ScriptedLLM({UNDERSTAND_INSTRUCTIONS: [{**candidates(), "intent": "memory_list", "candidates": []}]})
    result = await agent(llm, FakeProbe({}), stores).understand("asha", "what do you remember about me?", [], run_id="r1")
    assert result.request.intent == "memory_list" and result.request.topics == []


def test_model_facing_topic_schema_requires_every_field():
    from gem_agent.agents.chat import TopicOut

    assert set(TopicOut.model_json_schema()["required"]) == {
        "name", "description", "domain", "match_terms", "acronyms", "related_but_not_enough", "not_this"}


def test_topic_out_becomes_a_definition():
    from gem_agent.agents.chat import TopicOut

    topic = TopicOut.model_validate(topic_out(LOS)).to_topic("LOS tenders")
    assert topic.meaning == LOS.meaning and topic.acronyms == {"LOS": "Loan Origination System"}
    assert topic.user_text == "LOS tenders"


async def test_buyers_the_user_asked_for_become_part_of_the_topic(stores):
    llm = ScriptedLLM({UNDERSTAND_INSTRUCTIONS: [candidates((LOS, 0.9), buyer_hints=["banks"])]})
    result = await agent(llm, FakeProbe({}), stores).understand("asha", "LOS tenders from banks", [], run_id="r1")
    topic = result.request.topics[0]
    assert topic.buyers == ["banks"]
    assert "Buyers the user wants: banks" in topic.render()
    assert topic.key() != LOS.key()


async def test_buyer_only_needs_the_word_only(stores):
    llm = ScriptedLLM({UNDERSTAND_INSTRUCTIONS: [candidates((LOS, 0.9), buyer_hints=["cooperative banks"],
                                                            buyer_only=True)]})
    result = await agent(llm, FakeProbe({}), stores).understand("asha", "LOS tenders from cooperative banks", [],
                                                                run_id="r1")
    assert result.request.buyer_only is False
    assert result.request.buyer_hints == ["cooperative banks"]


async def test_progress_line_does_not_repeat_the_word_tenders(stores):
    rbi = LOS.model_copy(update={"meaning": "Reserve Bank of India (RBI) Tenders: procurement notices by the RBI"})
    llm = ScriptedLLM({UNDERSTAND_INSTRUCTIONS: [candidates((rbi, 0.9))]})
    result = await agent(llm, FakeProbe({rbi.meaning: 0}), stores).understand("asha", "central bank tenders", [], run_id="r1")
    assert "RBI) Tenders tenders" not in result.progress_line
    assert result.progress_line.startswith("Looking for Reserve Bank of India (RBI) Tenders published")


async def test_chosen_meanings_are_saved_for_next_time(stores):
    _, topics = stores
    llm = ScriptedLLM({UNDERSTAND_INSTRUCTIONS: [candidates((LOS, 0.9))]})
    await agent(llm, FakeProbe({LOS.meaning: 5}), stores).understand("asha", "LOS tenders", [], run_id="r1")
    assert [t.meaning for t in topics.get("asha", "LOS tenders")] == [LOS.meaning]
    llm2 = ScriptedLLM({UNDERSTAND_INSTRUCTIONS: [candidates((LOS, 0.9))]})
    await agent(llm2, FakeProbe({}), stores).understand("asha", "LOS tenders", [], run_id="r2")
    assert "los tenders → Loan Origination System" in llm2.calls[0][1]


@pytest.mark.parametrize("text,expected", [
    ("CBS tenders published in October", True), ("since 1 November", True), ("from 2026-11-01", True),
    ("published after 01/11/2026", True), ("ISO 27001 audit tenders", False), ("AML tenders FY 2026-27", False),
    ("top 10 bank tenders", False), ("AML tenders last 2 weeks", False)])
def test_mentions_dates(text, expected):
    assert mentions_dates(text) is expected


@pytest.mark.parametrize("text,expected", [
    ("only cooperative banks", True), ("Exclusively from PSU banks", True), ("from cooperative banks", False),
    ("the only LOS tenders", True), ("lonely tenders", False)])
def test_says_only(text, expected):
    assert says_only(text) is expected


def test_old_run_json_with_limit_still_loads():
    assert TenderRequest.model_validate({"intent": "search", "user_text": "x", "limit": 10}).intent == "search"
