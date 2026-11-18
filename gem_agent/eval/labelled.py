"""The labelled set (spec 13): bids labelled relevant or not for a fixed topic definition, from their documents, with
the quote that decides it. The Judge is scored against it: precision and recall, and how many relevant bids triage
dropped (target 0).

The labels are written by a reader who is not the Judge (Claude, from the cached documents) and reviewed by a person;
the Judge's own verdicts are never copied into them."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from gem_agent.agents.schemas import TopicDefinition, Verdict

Bucket = Literal["positive", "related", "trap", "random"]


class Label(BaseModel):
    topic: str  # key into LabelledSet.topics
    b_id: int
    bid_number: str
    title: str = ""
    bucket: Bucket  # why it was picked: a likely match, related-but-not-enough, a look-alike, or a random open bid
    relevant: bool
    unsure: bool = False
    quote: str = ""  # the words that decide it, copied from the documents
    file: str = ""
    page: int | None = None
    note: str = ""  # one sentence: what the tender buys and why that is (not) the topic
    labelled_by: str = ""
    to_review: bool = False  # picked for a person to check: every unsure label plus a random 10% of the rest
    reviewed_by: str | None = None  # the person who checked it; their answer replaces the label
    review_note: str = ""


class LabelledSet(BaseModel):
    version: int = 1
    created: datetime | None = None
    rulings: list[str] = []  # the user's rulings the labels follow
    topics: dict[str, TopicDefinition]
    items: list[Label]

    @classmethod
    def load(cls, path: Path) -> "LabelledSet":
        return cls.model_validate_json(path.read_text())

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=1) + "\n")


class Row(BaseModel):
    label: Label
    verdict: Verdict | None = None
    error: str | None = None

    @property
    def judged(self) -> bool | None:
        return None if self.verdict is None else self.verdict.relevant


class Score(BaseModel):
    topic: str
    n: int
    tp: int
    fp: int
    fn: int
    tn: int
    failed: int
    triage_lost: int  # labelled relevant, dropped by triage
    borderline: int = 0  # verdicts that ask the reader a question
    unsure: int = 0  # unsure labels that were judged
    borderline_on_unsure: int = 0  # borderline verdicts on unsure labels
    unsure_judged_borderline: int = 0  # unsure labels the Judge called borderline (same count, read from the label side)
    precision: float | None
    recall: float | None


def score(rows: list[Row], topic: str = "all") -> Score:
    """A borderline verdict is neither in nor out: it counts as found for recall (the reader sees it) but never as
    relevant, so precision is untouched."""
    ok = [r for r in rows if r.verdict is not None]
    border = [r for r in ok if r.verdict.borderline]
    tp = sum(r.label.relevant and r.judged for r in ok)
    fp = sum(not r.label.relevant and r.judged for r in ok)
    found_borderline = sum(r.label.relevant for r in border)
    fn = sum(r.label.relevant and not r.judged and not r.verdict.borderline for r in ok)
    tn = sum(not r.label.relevant and not r.judged and not r.verdict.borderline for r in ok)
    lost = sum(r.label.relevant and r.verdict.stage == "triage" and not r.verdict.relevant for r in ok)
    on_unsure = sum(r.label.unsure for r in border)
    found = tp + found_borderline
    return Score(topic=topic, n=len(rows), tp=tp, fp=fp, fn=fn, tn=tn, failed=len(rows) - len(ok), triage_lost=lost,
                 borderline=len(border), unsure=sum(r.label.unsure for r in ok), borderline_on_unsure=on_unsure,
                 unsure_judged_borderline=on_unsure,
                 precision=tp / (tp + fp) if tp + fp else None, recall=found / (found + fn) if found + fn else None)


def scores(rows: list[Row]) -> list[Score]:
    topics = sorted({r.label.topic for r in rows})
    return [score([r for r in rows if r.label.topic == t], t) for t in topics] + [score(rows)]


def report(rows: list[Row]) -> str:
    pct = lambda x: "—" if x is None else f"{x:.0%}"  # noqa: E731
    lines = [f"{'topic':<14}{'n':>4}{'tp':>4}{'fp':>4}{'fn':>4}{'tn':>4}  precision  recall  borderline  triage lost  failed"]
    for s in scores(rows):
        lines.append(f"{s.topic:<14}{s.n:>4}{s.tp:>4}{s.fp:>4}{s.fn:>4}{s.tn:>4}  {pct(s.precision):>9}  "
                     f"{pct(s.recall):>6}  {s.borderline:>10}  {s.triage_lost:>11}  {s.failed:>6}")
    total = scores(rows)[-1]
    judged = total.n - total.failed
    lines.append(f"\nBorderline rate: {total.borderline} of {judged} judged ({pct(total.borderline / judged if judged else None)}). "
                 f"Agreement with unsure labels: {total.borderline_on_unsure} of {total.borderline} borderline verdicts "
                 f"are on unsure labels; {total.unsure_judged_borderline} of {total.unsure} unsure labels judged borderline.")
    wrong = [r for r in rows if r.verdict is not None and not r.verdict.borderline and r.judged != r.label.relevant]
    if wrong:
        lines.append("\nDisagreements (label → judge):")
        for r in wrong:
            v = r.verdict
            lines.append(f"  {r.label.topic:<12} {r.label.bid_number:<22} {'in' if r.label.relevant else 'out'} → "
                         f"{'in' if v.relevant else 'out'} [{v.stage}{', unsure label' if r.label.unsure else ''}] "
                         f"{v.reason[:110]}")
    asked = [r for r in rows if r.verdict is not None and r.verdict.borderline]
    if asked:
        lines.append("\nBorderline (question):")
        for r in asked:
            lines.append(f"  {r.label.topic:<12} {r.label.bid_number:<22} label {'in' if r.label.relevant else 'out'}"
                         f"{' (unsure)' if r.label.unsure else ''}: {r.verdict.question}")
    for r in rows:
        if r.error:
            lines.append(f"  FAILED {r.label.bid_number}: {r.error[:120]}")
    return "\n".join(lines)


def rows_json(rows: list[Row]) -> str:
    return json.dumps([{"topic": r.label.topic, "bid": r.label.bid_number, "label": r.label.relevant,
                        "unsure": r.label.unsure, "judged": r.judged,
                        "stage": r.verdict.stage if r.verdict else None,
                        "triage": r.verdict.triage if r.verdict else None,
                        "borderline": r.verdict.borderline if r.verdict else None,
                        "question": r.verdict.question if r.verdict else None,
                        "reason": r.verdict.reason if r.verdict else r.error} for r in rows], indent=1)
