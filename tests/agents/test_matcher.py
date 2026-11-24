import re

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from gem_agent.agents.matcher import MATCH_INSTRUCTIONS, Matcher, MatchItem, MatchOut, buyer_line
from gem_agent.tools.gem.models import Bid
from tests.agents.helpers import LOS, ScriptedLLM


def item(b_id):
    return MatchItem(b_id=b_id, description=f"Loan origination description {b_id}", buyer="A bank")


def bid(ministry=None, department=None):
    return Bid(b_id=1, bid_number="GEM/2026/B/1", title="Loan origination system", bid_type=1,
               ministry=ministry, department=department)


def empty(*positions):
    return {"match": [], "unsure": list(positions)}


async def test_each_bid_gets_match_unsure_or_no():
    llm = ScriptedLLM({MATCH_INSTRUCTIONS: [{"match": [0], "unsure": [2]}]})
    matcher = Matcher(llm.model())

    assert await matcher.match(LOS, [item(101), item(102), item(103)]) == {
        101: "match", 102: "no", 103: "unsure",
    }
    assert matcher.calls == 1


async def test_a_number_in_both_lists_is_a_match_and_unknown_numbers_are_ignored():
    llm = ScriptedLLM({MATCH_INSTRUCTIONS: [{"match": [0, 7], "unsure": [0, -1]}]})
    matcher = Matcher(llm.model())

    assert await matcher.match(LOS, [item(101), item(102)]) == {101: "match", 102: "no"}


async def test_more_than_forty_are_sent_in_batches_of_forty():
    llm = ScriptedLLM({MATCH_INSTRUCTIONS: lambda prompt: {
        "match": [int(n) for n in re.findall(r"^\[(\d+)\]", prompt, re.MULTILINE)], "unsure": [],
    }})
    matcher = Matcher(llm.model())
    items = [item(1000 + n) for n in range(85)]

    assert await matcher.match(LOS, items) == {i.b_id: "match" for i in items}
    assert matcher.calls == 3
    assert [len(re.findall(r"^\[(\d+)\]", prompt, re.MULTILINE)) for _, prompt in llm.calls] == [40, 40, 5]


async def test_a_custom_batch_size_is_honored_up_to_forty():
    llm = ScriptedLLM({MATCH_INSTRUCTIONS: lambda prompt: {
        "match": [int(n) for n in re.findall(r"^\[(\d+)\]", prompt, re.MULTILINE)], "unsure": [],
    }})
    matcher = Matcher(llm.model(), batch=2)

    assert await matcher.match(LOS, [item(n) for n in range(5)]) == {n: "match" for n in range(5)}
    assert matcher.calls == 3
    assert [len(re.findall(r"^\[(\d+)\]", prompt, re.MULTILINE)) for _, prompt in llm.calls] == [2, 2, 1]


@pytest.mark.parametrize("batch", [0, -1, 41])
def test_batch_size_must_be_between_one_and_forty(batch):
    with pytest.raises(ValueError, match="between 1 and 40"):
        Matcher(ScriptedLLM({MATCH_INSTRUCTIONS: [empty()]}).model(), batch=batch)


async def test_a_failing_batch_is_split_and_a_half_that_fails_again_is_unsure():
    replies = iter([RuntimeError("bad batch"), {"match": [0, 1], "unsure": []}, RuntimeError("bad half")])

    def reply(prompt):
        answer = next(replies)
        if isinstance(answer, Exception):
            raise answer
        return answer

    llm = ScriptedLLM({MATCH_INSTRUCTIONS: reply})
    matcher = Matcher(llm.model())

    assert await matcher.match(LOS, [item(n) for n in range(1, 5)]) == {
        1: "match", 2: "match", 3: "unsure", 4: "unsure",
    }
    assert matcher.calls == 3


async def test_the_buyer_is_on_each_line():
    llm = ScriptedLLM({MATCH_INSTRUCTIONS: [empty()]})
    matcher = Matcher(llm.model())
    items = [
        MatchItem(b_id=1, description="Loan origination system",
                  buyer=buyer_line(bid("Ministry of Finance", "Department of Financial Services"), "NABARD")),
        MatchItem(b_id=2, description="Office chairs", buyer=buyer_line(bid(), None)),
    ]

    await matcher.match(LOS, items)

    prompt = llm.calls[0][1]
    assert "| buyer: Ministry of Finance / Department of Financial Services / NABARD" in prompt
    lines = prompt.split("Tenders:\n", 1)[1].splitlines()
    assert len(lines) == 2 and all("| buyer:" in line for line in lines)


@pytest.mark.parametrize(("ministry", "department", "org", "expected"), [
    ("Ministry of Finance", "Department of Financial Services", "NABARD",
     "Ministry of Finance / Department of Financial Services / NABARD"),
    (None, "Department of Revenue", None, "Department of Revenue"),
    ("Ministry of Finance", None, "A bank", "Ministry of Finance / A bank"),
    (None, None, None, ""),
    ("", "", "", ""),
])
def test_buyer_line_leaves_empty_parts_out(ministry, department, org, expected):
    assert buyer_line(bid(ministry, department), org) == expected


@pytest.mark.parametrize("payload", [{}, {"match": []}, {"unsure": []}])
def test_match_out_requires_both_number_lists(payload):
    with pytest.raises(ValidationError):
        MatchOut.model_validate(payload)


async def test_model_calls_use_zero_temperature_and_six_hundred_tokens():
    settings = []

    def reply(messages, info):
        settings.append(info.model_settings)
        return ModelResponse(parts=[TextPart('{"match": [], "unsure": []}')])

    matcher = Matcher(FunctionModel(reply, model_name="scripted"))
    await matcher.match(LOS, [item(1)])

    assert settings == [{"max_tokens": 600, "temperature": 0.0}]


async def test_invalid_output_is_retried_twice_within_the_batch():
    bad = {"match": "not a list", "unsure": []}
    good = {"match": [0, 1, 2, 3], "unsure": []}
    llm = ScriptedLLM({MATCH_INSTRUCTIONS: [bad, bad, good]})
    matcher = Matcher(llm.model())

    assert await matcher.match(LOS, [item(n) for n in range(1, 5)]) == {
        1: "match", 2: "match", 3: "match", 4: "match",
    }
    assert len(llm.calls) == 3
    assert matcher.calls == len(llm.calls)


async def test_no_items_means_no_model_call():
    llm = ScriptedLLM({MATCH_INSTRUCTIONS: [empty()]})
    matcher = Matcher(llm.model())

    assert await matcher.match(LOS, []) == {}
    assert matcher.calls == 0 and llm.calls == []
