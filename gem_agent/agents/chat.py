"""Chat agent (spec 5.3): works out what the user means, never asks them to confirm it.

1. One call proposes 1-3 candidate meanings (as TopicDefinitions), the date window, buyer hints, and any facts the
   user stated about themselves (saved to memory at once). Its prompt carries today's date, the user's memory and
   their saved meanings.
2. Each candidate is checked against the local index (probe_site, no site requests).
3. Only when two or more candidates remain, a second short call weighs them with that evidence and keeps one or two.
   Two kept meanings are both searched and the results grouped, so a wrong guess costs nothing.
The server enforces a JSON schema on each answer, which rules out tool calls in the same request; that is why the
probe runs between the two calls instead of as a tool.
"""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.models import Model

from gem_agent.agents.schemas import DateWindow, ProbeResult, ResolvedWindow, TenderRequest, TopicDefinition
from gem_agent.memory.topics import TopicStore
from gem_agent.memory.user_memory import MemoryStore
from gem_agent.tools.gem.models import site_now

UNDERSTAND_INSTRUCTIONS = """You turn a request for Indian government (GeM) tenders into a structured search. Never ask the user anything.
- intent: search (a new request), refine (narrows or corrects the current one), show_rejected, memory_list
  ("what do you remember"), memory_forget ("forget that"), or other.
- candidates: 1-3 plausible meanings of the topic, most likely first. Each candidate is ONE precise meaning:
  name (the short name, e.g. "Loan Origination System") and a one-sentence description of the thing itself, which
  the user reads as "Looking for <description>": e.g. "software banks use to take in, assess and approve loan
  applications", never "Tenders for …" or "Procurement of …". Never merge meanings.
  When the topic is an acronym or short term with several common expansions in different fields (banking,
  telecom, health, engineering, …), give each plausible expansion as its own candidate. Use what you know about
  the user and the rest of the request to rank them.
  When the request names a general class of product or service (payment systems, treasury, core banking), the
  candidate is the whole class: describe it broadly and list its specific products in match_terms; never narrow
  it to one product.
  match_terms: phrases and synonyms a tender would use, including the acronym. related_but_not_enough: adjacent
  things that alone don't count. not_this: the other meanings of the same words or acronym, and look-alike words.
- window: "within / in the last N weeks or days" means kind=published with last_days=N*7 or N, unless the user
  says closing; "closing in the next N days" means kind=closing with next_days=N. No dates mentioned: kind=open.
- buyer_hints: only buyers the user actually wrote ("cooperative banks", "central government"); usually empty.
- stated_facts: facts the user said about themselves or their business, in their words. Not requests."""

CHOOSE_INSTRUCTIONS = """You pick which meanings of a tender request to search, given what the user is like and how each meaning
fares against the titles of open tenders. Keep the most likely meaning first. Keep a second meaning only if it
is plausible and has real matching tenders. Never ask the user; give the reason in one sentence."""


# Model-facing schemas: every field is required. The server enforces the JSON schema, and a field with a default
# is one the model may leave out (16 Nov: it set last_days=14 but left kind at its default "open").


ShortText = Annotated[str, Field(max_length=150)]  # one phrase; lists of these are bounded item by item

# Free text the model writes is bounded: with an enforced schema, a field that runs past max_tokens cuts the JSON
# off and the whole call fails (16 Nov: a title screen's long "reason" failed two runs).
class AcronymOut(BaseModel):
    acronym: str = Field(max_length=20)
    expansion: str = Field(max_length=120)


class TopicOut(BaseModel):
    name: str = Field(max_length=80, description='short name, e.g. "Loan Origination System"')
    description: str = Field(max_length=400, description='one sentence on the thing itself, not "Tenders for …"')
    domain: str = Field(max_length=120)
    match_terms: list[ShortText] = Field(max_length=12)
    acronyms: list[AcronymOut] = Field(max_length=6)
    related_but_not_enough: list[ShortText] = Field(max_length=6)
    not_this: list[ShortText] = Field(max_length=8)

    def to_topic(self, user_text: str) -> TopicDefinition:
        return TopicDefinition(user_text=user_text, meaning=f"{self.name.strip()}: {self.description.strip()}",
                               domain=self.domain, match_terms=self.match_terms,
                               acronyms={a.acronym: a.expansion for a in self.acronyms},
                               related_but_not_enough=self.related_but_not_enough, not_this=self.not_this)


class WindowOut(BaseModel):
    kind: Literal["open", "published", "closing"]
    last_days: int | None
    next_days: int | None
    from_date: date | None
    to_date: date | None


class TopicCandidate(BaseModel):
    topic: TopicOut
    plausibility: float = Field(ge=0, le=1)


class UnderstandOutput(BaseModel):
    intent: Literal["search", "refine", "show_rejected", "memory_list", "memory_forget", "other"]
    candidates: list[TopicCandidate] = Field(max_length=3)
    window: WindowOut
    buyer_hints: list[ShortText] = Field(max_length=5)
    buyer_only: bool = Field(description='true only if the user wrote "only" or "exclusively" about the buyers; '
                                         '"from cooperative banks" alone is false')
    limit: int | None = Field(description="how many results the user asked for, if they said")
    stated_facts: list[ShortText] = Field(max_length=5)


class ChooseOutput(BaseModel):
    keep: list[int] = Field(min_length=1, max_length=2, description="indexes of the meanings to search")
    chosen_because: str = Field(max_length=400)


class Understanding(BaseModel):
    request: TenderRequest
    probes: list[ProbeResult]
    progress_line: str


def short_name(topic: TopicDefinition) -> str:
    return topic.meaning.split(":")[0].strip()[:80]


def _noun(name: str) -> str:
    """'… Tenders' already says tenders; otherwise add the word."""
    return name if re.search(r"tenders?$", name, re.IGNORECASE) else f"{name} tenders"


SAVED_MEANINGS_IN_PROMPT = 20  # most recent requests; older ones still load when the same text is asked again


class ChatAgent:
    def __init__(self, model: Model, *, probe: Callable[[TopicDefinition, ResolvedWindow], Awaitable[ProbeResult]],
                 memory: MemoryStore, topics: TopicStore, now: Callable[[], datetime] = site_now) -> None:
        self.probe, self.memory, self.topics, self.now = probe, memory, topics, now
        self._understand = Agent(model, output_type=NativeOutput(UnderstandOutput),
                                 instructions=UNDERSTAND_INSTRUCTIONS)
        self._choose = Agent(model, output_type=NativeOutput(ChooseOutput), instructions=CHOOSE_INSTRUCTIONS)

    def _context(self, user: str, history: list[str]) -> str:
        now = self.now()
        parts = [f"Today is {now:%A} {now.day} {now:%B %Y}, {now:%H:%M} IST."]
        facts = self.memory.prompt_facts(user)
        if facts:
            parts.append("What you know about this user:\n" + "\n".join(f"- {f.fact} ({f.evidence})" for f in facts))
        saved = self.topics.all(user, limit=SAVED_MEANINGS_IN_PROMPT)
        if saved:
            parts.append("Meanings this user used before:\n" + "\n".join(f"- {text} → {t.meaning}" for text, t in saved))
        if history:
            parts.append("Earlier in this chat:\n" + "\n".join(f"- {h}" for h in history[-6:]))
        return "\n\n".join(parts)

    async def understand(self, user: str, message: str, history: list[str], *, run_id: str) -> Understanding:
        context = self._context(user, history)
        out: UnderstandOutput = (await self._understand.run(
            f"{context}\n\nRequest: {message}", model_settings={"max_tokens": 1500, "temperature": 0.0})).output
        now = self.now()
        for fact in out.stated_facts:
            self.memory.add_stated(user, fact, run_id=run_id, today=now.date())
        window = DateWindow(**out.window.model_dump()).resolve(now)
        request = TenderRequest(intent=out.intent, user_text=message, window=window, buyer_hints=out.buyer_hints,
                                buyer_only=out.buyer_only and bool(out.buyer_hints), limit=out.limit)
        if not out.candidates:
            return Understanding(request=request, probes=[], progress_line="")

        ranked = sorted(out.candidates, key=lambda c: -c.plausibility)
        candidates = [(c.topic.to_topic(message), c.plausibility) for c in ranked]
        probes = [await self.probe(t, window) for t, _ in candidates]
        if len(candidates) == 1:
            keep, because = [0], "the only plausible meaning"
        else:
            listing = "\n\n".join(
                f"[{i}] {t.meaning} (plausibility {p_:.2f})\n"
                f"Open tenders in the window whose titles match: {p.strong} look relevant from their titles\n"
                f"Closest titles: " + "; ".join(p.top_titles[:5])
                for i, ((t, p_), p) in enumerate(zip(candidates, probes, strict=True)))
            choice: ChooseOutput = (await self._choose.run(
                f"{context}\n\nRequest: {message}\n\nMeanings:\n{listing}",
                model_settings={"max_tokens": 200, "temperature": 0.0})).output
            keep = [i for i in dict.fromkeys(choice.keep) if 0 <= i < len(candidates)] or [0]
            because = choice.chosen_because
        topics = [candidates[i][0] for i in keep]
        kept_probes = [probes[i] for i in keep]
        for i, topic in enumerate(topics):
            others = [t.meaning for j, t in enumerate(topics) if j != i]
            topics[i] = topic.model_copy(update={"chosen_because": because, "other_meanings_searched": others,
                                                 "buyers": list(out.buyer_hints)})
        request.topics = topics
        # Remember what this phrase meant, so the same request next time starts from it (spec 5.3, memory/topics.py)
        self.topics.save(user, message, topics)
        return Understanding(request=request, probes=kept_probes,
                             progress_line=self._progress_line(message, topics, kept_probes, window))

    @staticmethod
    def _progress_line(message: str, topics: list[TopicDefinition], probes: list[ProbeResult],
                       window: ResolvedWindow) -> str:
        """The first progress line: information, not a question (R11)."""
        if len(topics) == 1:
            p = probes[0]
            return (f"Looking for {_noun(short_name(topics[0]))} {window.describe()} "
                    f"({p.window_count:,} open bids in that window; {p.strong} look relevant from their titles).")
        groups = " · ".join(f"{short_name(t)} ({p.strong})" for t, p in zip(topics, probes, strict=True))
        return f"Searching two meanings: {groups}, {window.describe()}; results grouped, likeliest first."
