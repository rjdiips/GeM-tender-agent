"""Per-user facts with evidence: stated vs learned (spec 5.5). Stored in knowledge/users/<user>/memory.json.

- Stated facts ("we're a banking software company") are saved at once.
- Learned facts become active only when at least 2 separate runs support them.
- A contradicting run weakens a fact; at zero strength it is removed.
- The user can list, correct and forget facts. Memory never blocks anything: the request always wins.
"""
from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable
from datetime import date
from pathlib import Path

from pydantic import TypeAdapter

from gem_agent.agents.schemas import MemoryChange, MemoryFact

_FACTS = TypeAdapter(list[MemoryFact])
RUNS_TO_LEARN = 2


class FactNotFound(KeyError):
    """No such fact, or it was removed: a removed fact is not brought back by an edit."""


def _norm(text: str) -> str:
    text = re.sub(r"(\w)'re\b", r"\1 are", text.lower())
    return " ".join(re.findall(r"\w+", text))


def same_fact(a: str, b: str) -> bool:
    """The same fact up to case, punctuation and "we're" / "we are". A fuzzy ratio merged "fact 1" with "fact 12"."""
    return _norm(a) == _norm(b)


class MemoryStore:
    def __init__(self, knowledge_dir: Path) -> None:
        self.root = Path(knowledge_dir) / "users"

    def _path(self, user: str) -> Path:
        safe = re.sub(r"[^\w.-]", "_", user)
        return self.root / safe / "memory.json"

    def facts(self, user: str) -> list[MemoryFact]:
        path = self._path(user)
        return _FACTS.validate_json(path.read_bytes()) if path.exists() else []

    def _save(self, user: str, facts: list[MemoryFact]) -> None:
        path = self._path(user)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(_FACTS.dump_json(facts, indent=1))
        tmp.replace(path)

    def prompt_facts(self, user: str, limit: int = 20) -> list[MemoryFact]:
        """Active facts, newest and strongest first, for the Chat agent's prompt."""
        active = [f for f in self.facts(user) if f.active and not f.removed and f.kind == "general"]
        return sorted(active, key=lambda f: (f.since, f.strength), reverse=True)[:limit]

    def add_stated(self, user: str, fact: str, *, run_id: str, today: date, evidence: str | None = None) -> MemoryFact:
        facts = self.facts(user)
        existing = next((f for f in facts if same_fact(f.fact, fact) and not f.removed and f.kind == "general"), None)
        if existing:
            existing.source, existing.active, existing.since = "stated", True, today
            existing.strength = max(existing.strength, RUNS_TO_LEARN)
            new = existing
        else:
            new = MemoryFact(id=uuid.uuid4().hex[:8], fact=fact, evidence=evidence or f"you said so on {today:%d %b}",
                             source="stated", since=today, runs=[run_id], strength=RUNS_TO_LEARN, active=True)
            facts.append(new)
        self._save(user, facts)
        return new

    def add_ruling(self, user: str, fact: str, *, topic: str, run_id: str, today: date, evidence: str) -> MemoryFact:
        """A ruling is always a new fact: its identity is its id, so it never merges with another."""
        facts = self.facts(user)
        new = MemoryFact(id=uuid.uuid4().hex[:8], fact=fact, evidence=evidence, source="stated", since=today,
                         runs=[run_id], strength=RUNS_TO_LEARN, active=True, kind="ruling", topic=topic)
        facts.append(new)
        self._save(user, facts)
        return new

    def ruling_facts(self, user: str, topic: str) -> list[MemoryFact]:
        return [f for f in self.facts(user) if f.kind == "ruling" and f.topic == topic and f.active and not f.removed]

    def apply(self, user: str, changes: Iterable[MemoryChange], *, run_id: str, today: date) -> None:
        facts = self.facts(user)
        by_id = {f.id: f for f in facts}
        for change in changes:
            target = by_id.get(change.fact_id or "")
            if target is not None and target.kind != "general":
                continue  # a ruling is changed only by the user, never by the memory step
            if change.action == "add" and change.fact:
                target = next((f for f in facts if same_fact(f.fact, change.fact) and not f.removed and f.kind == "general"), None)
                if target is None:
                    target = MemoryFact(id=uuid.uuid4().hex[:8], fact=change.fact, evidence=change.evidence,
                                        source="learned", since=today)
                    facts.append(target)
                    by_id[target.id] = target
                self._support(target, run_id, change.evidence, today)
            elif target is None:
                continue
            elif change.action == "support":
                self._support(target, run_id, change.evidence, today)
            elif change.action == "weaken":
                target.strength -= 1
                target.evidence = change.evidence
                if target.strength <= 0:
                    target.active, target.removed = False, True
            elif change.action == "remove":
                target.active, target.removed = False, True
            elif change.action == "update" and change.fact:
                target.fact, target.evidence, target.since = change.fact, change.evidence, today
        self._save(user, facts)

    @staticmethod
    def _support(fact: MemoryFact, run_id: str, evidence: str, today: date) -> None:
        if run_id in fact.runs:
            return
        fact.runs.append(run_id)
        fact.evidence, fact.since = evidence, today
        fact.strength += 1
        if fact.source == "stated" or len(fact.runs) >= RUNS_TO_LEARN:
            fact.active = True

    def forget(self, user: str, fact_id: str, evidence: str | None = None) -> None:
        facts = self.facts(user)
        for f in facts:
            if f.id == fact_id:
                f.active, f.removed = False, True
                if evidence is not None:
                    f.evidence = evidence
        self._save(user, facts)

    def correct(self, user: str, fact_id: str, text: str, *, today: date) -> None:
        """A correction from the user: it becomes a stated fact. A removed fact stays removed."""
        facts = self.facts(user)
        f = next((f for f in facts if f.id == fact_id and not f.removed), None)
        if f is None:
            raise FactNotFound(fact_id)
        f.fact, f.source, f.since, f.active = text, "stated", today, True
        f.evidence = f"you corrected it on {today:%d %b}"
        f.strength = max(f.strength, RUNS_TO_LEARN)
        self._save(user, facts)

    def forget_all(self, user: str) -> None:
        path = self._path(user)
        if path.exists():
            path.write_text(json.dumps([]), encoding="utf-8")
