"""Match tender descriptions to one topic, in bounded batches."""
from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel
from pydantic_ai import Agent, ModelRequestContext, ModelResponse, NativeOutput, RunContext
from pydantic_ai.capabilities import Hooks, WrapModelRequestHandler
from pydantic_ai.models import Model

from gem_agent.agents.schemas import TopicDefinition
from gem_agent.tools.gem.models import Bid

log = logging.getLogger(__name__)

MATCH_PROMPT_VERSION = "2"  # bump whenever MATCH_INSTRUCTIONS changes

MATCH_INSTRUCTIONS = """You decide, for one topic, which Indian government (GeM) tenders could be buying it, from a short
description of what each tender buys.
For every tender, choose exactly one of:
match: the topic itself is plainly in scope.
unsure: it might be: a broad platform or programme that could include the topic, a related-but-not-enough item that
may come with it, or a description too thin to tell.
no: plainly about something else. Look-alikes listed under "NOT this" are no.
Return three lists named match, unsure and no. Each bracketed number must appear in exactly one list; do not omit a
tender. Answer only with those three lists: no explanations.
Each line ends with the buyer. A topic about a buyer (a ministry, department or bank) matches on the buyer as well as on what is bought."""

Call = Literal["match", "unsure", "no"]


class MatchItem(BaseModel):
    b_id: int
    description: str
    buyer: str


class MatchOut(BaseModel):
    match: list[int]
    unsure: list[int]
    no: list[int]


def buyer_line(bid: Bid, buyer_org: str | None) -> str:
    """The listing's ministry and department, plus the buyer named in the bid document."""
    return " / ".join(part for part in (bid.ministry, bid.department, buyer_org) if part)


class Matcher:
    def __init__(self, model: Model, *, batch: int = 40, max_tokens: int = 600) -> None:
        if not 1 <= batch <= 40:
            raise ValueError("batch must be between 1 and 40")
        self.calls = 0
        self._batch = batch
        self._max_tokens = max_tokens
        self._agent = Agent(model, output_type=NativeOutput(MatchOut), instructions=MATCH_INSTRUCTIONS, retries=2,
                            capabilities=[Hooks(model_request=self._count_request)])

    async def _count_request(self, ctx: RunContext[None], *, request_context: ModelRequestContext,
                             handler: WrapModelRequestHandler) -> ModelResponse:
        """Count every model request, including output-validation retries and failed attempts."""
        self.calls += 1
        return await handler(request_context)

    async def match(self, topic: TopicDefinition, items: list[MatchItem]) -> dict[int, Call]:
        """One decision per bid: a failed batch is halved once, then an unanswered half is unsure."""
        decided: dict[int, Call] = {}
        for start in range(0, len(items), self._batch):
            batch = items[start : start + self._batch]
            out = await self._match_batch(topic, batch)
            if out is not None:
                decided.update(self._decide(batch, out))
                continue
            half = (len(batch) + 1) // 2
            for part in (batch[:half], batch[half:]):
                if not part:
                    continue
                out = await self._match_batch(topic, part)
                if out is None:
                    decided.update((item.b_id, "unsure") for item in part)
                else:
                    decided.update(self._decide(part, out))
        return decided

    async def _match_batch(self, topic: TopicDefinition, batch: list[MatchItem]) -> MatchOut | None:
        listing = "\n".join(f"[{i}] {item.description} | buyer: {item.buyer}" for i, item in enumerate(batch))
        try:
            out: MatchOut = (await self._agent.run(
                f"{topic.render()}\n\nTenders:\n{listing}",
                model_settings={"max_tokens": self._max_tokens, "temperature": 0.0},
            )).output
        except Exception as exc:  # noqa: BLE001 - validation failure after retries, timeouts, …
            log.warning("description match failed (%s) for %d tenders", exc, len(batch))
            return None
        chosen = [*out.match, *out.unsure, *out.no]
        if sorted(chosen) != list(range(len(batch))):
            log.warning("description match did not classify each of %d tenders exactly once: %s", len(batch), chosen[:50])
            return None
        return out

    @staticmethod
    def _decide(batch: list[MatchItem], out: MatchOut) -> dict[int, Call]:
        matched = set(out.match)
        return {item.b_id: "match" if i in matched else "unsure" if i in out.unsure else "no"
                for i, item in enumerate(batch)}
