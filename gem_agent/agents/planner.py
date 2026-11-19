"""Planner (spec 5.2): a SearchPlan from real site values only (R2).

The candidates (categories found for the topic, the ministries in the index, departments matching the user's
buyer hints) are looked up first and become enums in the answer's JSON schema. vLLM enforces that schema, so the
model can only pick values that exist; validation would reject anything else and the agent asks again.
"""
from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, Field, create_model
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.models import Model

from gem_agent.agents.schemas import SearchPlan, TenderRequest, TopicDefinition
from gem_agent.tools.gem.lookups import Category

PLAN_INSTRUCTIONS = """You plan a search of Indian government (GeM) tenders for one topic.
Every term runs as a live GeM title search, and GeM matches mainly on a term's first word, so give terms whose first word is specific ("UPI", "RTGS", "Murex"), not "digital …" or "online …". Base searches for bank IT and consultancy bids always run as well, so these are extras:
- categories: GeM categories that tenders on this topic are filed under; pick only clear fits (they run as extra
  live searches). Many relevant tenders sit in vague categories, so leave it empty if none fit.
- ministries / departments: the ones that best match the user's buyer hints (if any are offered). Leave them
  empty when unsure: they rank results, and the documents name the actual buyer.
- bid_types: 1 bid, 2 reverse auction, 5 direct RA; leave empty for all.
- extra_terms: a few more phrases a tender title on this topic would use, if the topic's own terms miss some.
- reasons: one sentence."""

NONE = "(none)"


class Lookups(Protocol):
    def find_categories(self, query: str, terms: list[str], k: int = 15) -> list[Category]: ...
    def ministries(self) -> list[str]: ...
    def departments(self, hints: list[str], k: int = 30) -> list[str]: ...


def _choice(values: list[str]) -> object:
    return Literal[tuple(values or [NONE])]  # type: ignore[valid-type]


def plan_output_model(ministries: list[str], departments: list[str], categories: list[str]) -> type[BaseModel]:
    return create_model(
        "PlanOutput",
        ministries=(list[_choice(ministries)], ...),  # all required: see schemas.KeyFacts
        departments=(list[_choice(departments)], ...),
        categories=(list[_choice(categories)], Field(max_length=5)),
        bid_types=(list[Literal[1, 2, 5]], ...),
        extra_terms=(list[str], Field(max_length=8)),
        reasons=(str, ...),
    )


class Planner:
    def __init__(self, model: Model, lookups: Lookups) -> None:
        self.model, self.lookups = model, lookups

    async def plan(self, request: TenderRequest, topic: TopicDefinition, note: str = "") -> SearchPlan:
        """`note` carries the graph's request to widen or narrow after a search (spec 5.1)."""
        terms = list(dict.fromkeys([*topic.match_terms, *topic.acronyms]))
        categories = self.lookups.find_categories(topic.meaning, terms)
        hints = request.buyer_hints
        ministries = self.lookups.ministries() if hints else []
        departments = self.lookups.departments(hints) if hints else []
        output = plan_output_model(ministries, departments, [c.value for c in categories])
        agent = Agent(self.model, output_type=NativeOutput(output), instructions=PLAN_INSTRUCTIONS, retries=2)
        lines = [topic.render(), f"\nRequest: {request.user_text}", f"Date window: {request.window.describe()}"]
        if request.buyer_hints:
            lines.append("Buyer hints from the user: " + "; ".join(request.buyer_hints))
        lines.append("\nCandidate categories (label (value)):\n" + "\n".join(f"- {c.label} ({c.value})" for c in categories))
        # buyer names come from GeM's listings: one "- name" line each inside a tagged block, so no name can forge a
        # separator or end the list
        if departments:
            lines.append("\nDepartments matching the buyer hints, one per line:\n<departments>\n"
                         + "\n".join(f"- {d}" for d in departments) + "\n</departments>")
        if ministries:
            lines.append("\nMinistries, one per line:\n<ministries>\n"
                         + "\n".join(f"- {m}" for m in ministries) + "\n</ministries>")
        if note:
            lines.append(f"\nNote: {note}")
        out = (await agent.run("\n".join(lines), model_settings={"max_tokens": 500, "temperature": 0.0})).output
        def pick(values: list[str]) -> list[str]:
            return [v for v in values if v != NONE]

        chosen_ministries, chosen_departments = pick(out.ministries), pick(out.departments)
        hard = request.buyer_only
        return SearchPlan(
            topic_key=topic.key(),
            text=topic.meaning + ("; " + "; ".join(topic.match_terms) if topic.match_terms else ""),
            terms=list(dict.fromkeys([*terms, *out.extra_terms])),
            **request.window.model_dump(),
            ministries=chosen_ministries if hard else [], departments=chosen_departments if hard else [],
            prefer_ministries=[] if hard else chosen_ministries, prefer_departments=[] if hard else chosen_departments,
            bid_types=list(out.bid_types),
            categories=pick(out.categories), reasons=out.reasons,
        )
