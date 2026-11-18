"""Post-run memory step (spec 5.5): one short model call reads what the user did in a run and proposes changes
to their memory. The store applies them: learned facts need 2 separate runs, contradictions weaken."""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.models import Model

from gem_agent.agents.schemas import MemoryChange
from gem_agent.memory.user_memory import MemoryStore

MEMORY_INSTRUCTIONS = """You keep a short memory about one user of a government-tender search tool, learned from what they do.
Read the run below and propose changes to their facts:
- add: a new fact the run suggests (their interests, buyers they keep, what an acronym means to them).
- support: an existing fact this run agrees with. weaken: an existing fact this run contradicts.
- remove: a fact that is clearly wrong now. update: reword an existing fact.
Use existing fact ids for support/weaken/remove/update. Facts are short and general, not about one tender.
Propose nothing if the run shows nothing new."""


class RunSignals(BaseModel):
    run_id: str
    request: str
    understood: str
    kept: list[str] = []  # bids the user kept (relevant and not rejected)
    downloaded: list[str] = []
    restored: list[str] = []  # restored from quarantine
    ignored: list[str] = []  # results never opened
    corrections: list[str] = []  # "no, I meant …"


class MemoryProposal(BaseModel):
    changes: list[MemoryChange]


class MemoryStep:
    def __init__(self, model: Model, store: MemoryStore) -> None:
        self.store = store
        self._agent = Agent(model, output_type=NativeOutput(MemoryProposal), instructions=MEMORY_INSTRUCTIONS)

    async def after_run(self, user: str, signals: RunSignals, *, today: date) -> list[MemoryChange]:
        facts = [f for f in self.store.facts(user) if not f.removed and f.kind == "general"]
        listing = "\n".join(
            f"[{f.id}] {f.fact} ({f.source}, {'active' if f.active else 'pending'}, strength {f.strength}): {f.evidence}"
            for f in facts) or "(none yet)"
        run = [f"Asked: {signals.request}", f"Understood as: {signals.understood}"]
        for label, items in (("Kept", signals.kept), ("Downloaded", signals.downloaded),
                             ("Restored from quarantine", signals.restored), ("Ignored", signals.ignored),
                             ("Corrections", signals.corrections)):
            if items:
                run.append(f"{label}: " + "; ".join(items))
        prompt = f"Current facts:\n{listing}\n\nThis run:\n" + "\n".join(run)
        out: MemoryProposal = (await self._agent.run(
            prompt, model_settings={"max_tokens": 400, "temperature": 0.0})).output
        self.store.apply(user, out.changes, run_id=signals.run_id, today=today)
        return out.changes
