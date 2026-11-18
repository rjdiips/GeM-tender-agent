from datetime import datetime
from pathlib import Path

import pytest

from gem_agent.agents.schemas import TopicDefinition, Verdict
from gem_agent.eval.labelled import Label, LabelledSet, Row, report, score, scores

TOPIC = TopicDefinition(user_text="AML", meaning="Anti-money laundering software")


def label(b_id: int, relevant: bool, topic: str = "aml", unsure: bool = False) -> Label:
    return Label(topic=topic, b_id=b_id, bid_number=f"GEM/2026/B/{b_id}", bucket="positive", relevant=relevant,
                 unsure=unsure)


def verdict(b_id: int, relevant: bool, stage: str = "judge") -> Verdict:
    return Verdict(b_id=b_id, bid_number=f"GEM/2026/B/{b_id}", topic_key="k", doc_hash="h", relevant=relevant,
                   confidence=0.9, stage=stage, triage="maybe", reason=f"reason {b_id}", model="m",
                   decided_at=datetime(2026, 11, 17))


def test_score_counts_precision_recall_and_triage_losses():
    rows = [Row(label=label(1, True), verdict=verdict(1, True)),
            Row(label=label(2, True), verdict=verdict(2, False, stage="triage")),
            Row(label=label(3, False), verdict=verdict(3, True)),
            Row(label=label(4, False), verdict=verdict(4, False)),
            Row(label=label(5, True), error="no documents")]
    s = score(rows)
    assert (s.tp, s.fn, s.fp, s.tn, s.failed, s.triage_lost) == (1, 1, 1, 1, 1, 1)
    assert s.precision == pytest.approx(0.5) and s.recall == pytest.approx(0.5)


def test_no_positives_gives_no_recall_rather_than_zero():
    s = score([Row(label=label(1, False), verdict=verdict(1, False))])
    assert s.recall is None and s.precision is None


def test_scores_per_topic_plus_total():
    rows = [Row(label=label(1, True, "aml"), verdict=verdict(1, True)),
            Row(label=label(2, True, "los"), verdict=verdict(2, False))]
    assert [(s.topic, s.tp) for s in scores(rows)] == [("aml", 1), ("los", 0), ("all", 1)]


def test_report_lists_disagreements_and_unsure_labels():
    rows = [Row(label=label(1, True, unsure=True), verdict=verdict(1, False)),
            Row(label=label(2, False), verdict=verdict(2, False))]
    text = report(rows)
    assert "GEM/2026/B/1" in text and "in → out" in text and "unsure label" in text
    assert "GEM/2026/B/2" not in text.split("Disagreements")[1]


def test_set_round_trips(tmp_path: Path):
    s = LabelledSet(topics={"aml": TOPIC}, items=[label(1, True)], rulings=["hardware-only AI is not AI"])
    s.save(tmp_path / "set.json")
    back = LabelledSet.load(tmp_path / "set.json")
    assert back.items[0].b_id == 1 and back.topics["aml"].meaning == TOPIC.meaning and back.rulings == s.rulings
