"""Rulings agent: matches a borderline question to the user's earlier rulings, and writes a new ruling as a fact.

Both answers are small and bounded. The resolve answer's fact_id is an enum of the offered ids plus "none", so the
model can only name a ruling that exists; half an answer (an id without a decision, or the reverse) decides nothing.
"""
from __future__ import annotations

from typing import Literal, Protocol, cast

from pydantic import BaseModel, Field, create_model
from pydantic_ai import Agent, ModelSettings, NativeOutput
from pydantic_ai.models import Model

from gem_agent.agents.chat import short_name
from gem_agent.agents.schemas import MemoryFact, TopicDefinition

RESOLVE_INSTRUCTIONS = """You decide whether one of the user's earlier rulings answers a new question about a tender.
Each ruling is a general rule the user gave for this topic. Answer only when a ruling covers the same case as the
question: then give its id and its decision (counts: the tender is relevant; not: it is not). If no ruling covers
the same case, answer none for both. Do not stretch a ruling to a different case."""

FACT_INSTRUCTIONS = """You turn a user's answer to a borderline question into a lasting rule.
Write the answer as one general sentence that covers similar tenders for this topic. Name the kind of case, not the
specific tender, so the sentence applies to future tenders of that kind."""

SETTINGS_RESOLVE: ModelSettings = {"max_tokens": 120, "temperature": 0.0}
SETTINGS_FACT: ModelSettings = {"max_tokens": 100, "temperature": 0.0}


class RulingMatch(BaseModel):
    fact_id: str
    decision: Literal["counts", "not"]


class FactOut(BaseModel):
    fact: str = Field(max_length=200)


class _ResolveOut(Protocol):
    """What resolve_output_model's create_model output carries; the fields mirror its field list."""
    fact_id: str
    decision: Literal["counts", "not", "none"]
    reason: str


def resolve_output_model(fact_ids: list[str]) -> type[BaseModel]:
    ids = Literal[tuple([*fact_ids, "none"])]  # type: ignore[valid-type]
    return create_model(
        "ResolveOutput",
        fact_id=(ids, ...),  # pyright: ignore[reportArgumentType]  # Literal built from the offered ids at runtime
        decision=(Literal["counts", "not", "none"], ...),
        reason=(str, Field(max_length=200)),
    )


class RulingsAgent:
    def __init__(self, model: Model) -> None:
        self.model = model

    async def resolve(self, topic: TopicDefinition, question: str, reason: str,
                      facts: list[MemoryFact]) -> RulingMatch | None:
        if not facts:
            return None
        agent = Agent(self.model, output_type=NativeOutput(resolve_output_model([f.id for f in facts])),
                      instructions=RESOLVE_INSTRUCTIONS, retries=2)
        prompt = "\n".join([
            f"Topic: {short_name(topic)}", topic.render(),
            f"\nQuestion about a tender: {question}", f"Why it is borderline: {reason}",
            "\nThe user's earlier rulings:", *(f"- {f.id}: {f.fact}" for f in facts)])
        out: _ResolveOut = cast(_ResolveOut, (await agent.run(prompt, model_settings=SETTINGS_RESOLVE)).output)
        if out.fact_id == "none" or out.decision == "none":
            return None
        return RulingMatch(fact_id=out.fact_id, decision=out.decision)

    async def write_fact(self, topic: TopicDefinition, question: str, decision: Literal["counts", "not"]) -> str:
        agent = Agent(self.model, output_type=NativeOutput(FactOut), instructions=FACT_INSTRUCTIONS, retries=2)
        answer = "counts (the tender is relevant)" if decision == "counts" else "not (the tender is not relevant)"
        prompt = "\n".join([f"Topic: {short_name(topic)}", topic.render(),
                            f"\nQuestion: {question}", f"The user's answer: {answer}"])
        return (await agent.run(prompt, model_settings=SETTINGS_FACT)).output.fact
