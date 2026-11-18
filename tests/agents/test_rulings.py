from datetime import date

from gem_agent.agents.rulings import FACT_INSTRUCTIONS, RESOLVE_INSTRUCTIONS, RulingMatch, RulingsAgent
from gem_agent.agents.schemas import MemoryFact
from tests.agents.helpers import LOS as TOPIC
from tests.agents.helpers import ScriptedLLM

Q = "Does a loan module inside a core banking tender count?"


def fact(fact_id: str, text: str = "A loan module inside core banking counts") -> MemoryFact:
    return MemoryFact(id=fact_id, fact=text, evidence="ruling", source="stated", since=date(2026, 11, 18),
                      kind="ruling", topic="Loan Origination System")


async def test_no_facts_means_no_call():
    llm = ScriptedLLM({})
    agent = RulingsAgent(llm.model())
    assert await agent.resolve(TOPIC, Q, "r", []) is None
    assert llm.calls == []


async def test_a_matching_fact_decides():
    llm = ScriptedLLM({RESOLVE_INSTRUCTIONS: [{"fact_id": "f1", "decision": "counts", "reason": "same case"}]})
    agent = RulingsAgent(llm.model())
    assert await agent.resolve(TOPIC, Q, "r", [fact("f1")]) == RulingMatch(fact_id="f1", decision="counts")


async def test_none_means_undecided():
    for reply in ({"fact_id": "none", "decision": "none", "reason": "different case"},
                  {"fact_id": "f1", "decision": "none", "reason": "half"},
                  {"fact_id": "none", "decision": "not", "reason": "half"}):
        llm = ScriptedLLM({RESOLVE_INSTRUCTIONS: [reply]})
        assert await RulingsAgent(llm.model()).resolve(TOPIC, Q, "r", [fact("f1")]) is None


async def test_the_prompt_carries_topic_question_and_each_fact_with_its_id():
    llm = ScriptedLLM({RESOLVE_INSTRUCTIONS: [{"fact_id": "none", "decision": "none", "reason": "x"}]})
    await RulingsAgent(llm.model()).resolve(TOPIC, Q, "r", [fact("f1", "first text"), fact("f2", "second text")])
    prompt = llm.calls[-1][1]
    for part in (TOPIC.meaning, Q, "f1", "first text", "f2", "second text"):
        assert part in prompt


async def test_write_fact_returns_the_general_fact():
    text = "AI that is one part of a larger system counts as an AI platform"
    llm = ScriptedLLM({FACT_INSTRUCTIONS: [{"fact": text}]})
    out = await RulingsAgent(llm.model()).write_fact(TOPIC, Q, "counts")
    assert out == text
    prompt = llm.calls[-1][1]
    assert TOPIC.meaning in prompt and Q in prompt and "counts" in prompt
