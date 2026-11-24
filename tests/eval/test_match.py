from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from gem_agent.agents.judge import doc_hash
from gem_agent.agents.schemas import TopicDefinition, TriageOutput, Verdict
from gem_agent.cache.docs import DocCache
from gem_agent.cli import main
from gem_agent.config import Settings
from gem_agent.eval.labelled import Label, LabelledSet
from gem_agent.eval.match import (
    BUYER_TOPICS,
    MatchReport,
    WayScore,
    checked,
    describe_labelled,
    evaluate,
    report_text,
    score_way,
)
from gem_agent.index.db import BidIndex, Description
from gem_agent.index.describe import DESCRIBE_INSTRUCTIONS, Describer
from gem_agent.tools.gem.models import Bid
from tests.agents.helpers import ScriptedLLM, bid_files
from tests.index.helpers import row as listing_row

NOW = datetime(2026, 11, 24)
AML = TopicDefinition(user_text="AML", meaning="Anti-money laundering software")
MOF = TopicDefinition(user_text="Ministry of Finance", meaning="Tenders from the Ministry of Finance")


def label(b_id: int, *, topic: str = "aml", relevant: bool = True, title: str | None = None,
          to_review: bool = False, reviewed_by: str | None = None) -> Label:
    return Label(topic=topic, b_id=b_id, bid_number=f"GEM/2026/B/{b_id}", title=title or f"Tender {b_id}",
                 bucket="positive" if relevant else "trap", relevant=relevant, to_review=to_review,
                 reviewed_by=reviewed_by)


def description(b_id: int, *, subject: str = "Anti-money laundering platform", has_scope: bool = True,
                doc_hash_value: str = "hash", model: str = "scripted", prompt_version: str = "1") -> Description:
    return Description(b_id=b_id, doc_hash=doc_hash_value, subject=subject, terms=["AML"], sources=["bid.pdf"],
                       has_scope=has_scope, buyer_org="A bank", model=model, prompt_version=prompt_version,
                       written_at=NOW)


def test_score_way_counts_checked_and_all_misses_and_non_matches_read():
    labelled = LabelledSet(topics={"aml": AML, "mof": MOF}, items=[
        label(1),
        label(2, title="Unreviewed AML tender", to_review=True),
        label(3, title="Reviewed AML tender", to_review=True, reviewed_by="person"),
        label(4, relevant=False),
        label(5, relevant=False),
        label(6, topic="mof"),
        label(7, topic="mof", relevant=False),
    ])
    reads = {(1, "aml"): False, (2, "aml"): False, (3, "aml"): True, (4, "aml"): True,
             (5, "aml"): False, (6, "mof"): False, (7, "mof"): True}

    score = score_way("today", reads, labelled, topics="subject", run=2, model_calls=17,
                      descriptions={1: description(1, subject="AML compliance suite")})

    assert score.way == "today" and score.topics == "subject" and score.run == 2
    assert (score.checked_missed, score.all_missed, score.negatives_read, score.negatives, score.model_calls) == \
        (1, 2, 1, 2, 17)
    assert score.missed == ["aml:1: AML compliance suite; AML", "aml:2: Unreviewed AML tender"]


def test_buyer_topics_are_scored_apart():
    assert BUYER_TOPICS == frozenset({"mof"})
    labelled = LabelledSet(topics={"aml": AML, "mof": MOF}, items=[
        label(1), label(2, relevant=False), label(3, topic="mof"), label(4, topic="mof", relevant=False),
    ])
    reads = {(1, "aml"): False, (2, "aml"): True, (3, "mof"): False, (4, "mof"): True}

    subject = score_way("A", reads, labelled, topics="subject", run=1, model_calls=1, descriptions={})
    buyer = score_way("A", reads, labelled, topics="buyer", run=1, model_calls=1, descriptions={})

    assert (subject.checked_missed, subject.all_missed, subject.negatives, subject.negatives_read) == (1, 1, 1, 1)
    assert (buyer.checked_missed, buyer.all_missed, buyer.negatives, buyer.negatives_read) == (1, 1, 1, 1)
    assert subject.missed == ["aml:1: Tender 1"] and buyer.missed == ["mof:3: Tender 3"]


def test_checked_excludes_only_unreviewed_labels():
    assert checked(label(1))
    assert not checked(label(1, to_review=True))
    assert checked(label(1, to_review=True, reviewed_by="person"))


class FakeDescriber:
    model_name = "scripted"
    prompt_version = "1"

    def __init__(self) -> None:
        self.described: list[int] = []

    def known(self, b_ids):
        return {}

    def stale(self, b_id, files):
        return False

    async def describe(self, bid, files):
        self.described.append(bid.b_id)
        return description(bid.b_id, doc_hash_value=doc_hash(files))


class FakeSearcher:
    def __init__(self, *, dropped: set[int] | None = None) -> None:
        self.settings = SimpleNamespace(screen_batch=40)
        self.dropped = dropped or set()
        self.calls = []

    async def _screen_titles(self, topic, bids, descriptions=None):
        self.calls.append((topic, list(bids), descriptions))
        return {bid.b_id for bid in bids if bid.b_id not in self.dropped}, set()


class FakeJudge:
    def __init__(self, *, read: bool = True) -> None:
        self.read = read
        self.verdicts = None
        self.cache_roots = []

    async def screen(self, topic, bid, files):
        self.cache_roots.append(self.verdicts.root)
        if self.read:
            return None, TriageOutput(decision="maybe", reason="needs a full read")
        verdict = Verdict(b_id=bid.b_id, bid_number=bid.bid_number, topic_key=topic.key(), doc_hash=doc_hash(files),
                          relevant=False, confidence=0.9, stage="triage", triage="clearly_no", reason="something else",
                          model="scripted", decided_at=NOW)
        return verdict, TriageOutput(decision="clearly_no", reason="something else")


class FakeMatcher:
    def __init__(self, reply) -> None:
        self.reply = reply
        self.calls = 0
        self.runs: dict[str, int] = {}

    async def match(self, topic, items):
        self.calls += 1
        run = self.runs.get(topic.user_text, 0)
        self.runs[topic.user_text] = run + 1
        return {item.b_id: self.reply(topic, run, item.b_id) for item in items}


def evaluation_parts(tmp_path: Path, items: list[Label], matcher: FakeMatcher, *, judge=None, searcher=None):
    index = BidIndex(tmp_path / "bids.sqlite")
    bids = {item.b_id: Bid.from_listing(listing_row(item.b_id, NOW, datetime(2026, 11, 28), item.title))
            for item in items}
    index.upsert(bids.values(), seen_at=NOW)
    cache = DocCache(tmp_path / "docs")
    for bid in bids.values():
        cache.save(bid_files(cache, bid.b_id, [("bid.pdf", "bid_document", [f"What tender {bid.b_id} buys"])]))
    labelled = LabelledSet(topics={"aml": AML, "mof": MOF}, items=items)
    return labelled, index, cache, FakeDescriber(), matcher, judge or FakeJudge(), searcher or FakeSearcher()


@pytest.mark.parametrize(("subject_calls", "retire", "ending"), [
    (["match", "match", "match"], True,
     ("A alone missed no checked label in 3 of 3 runs: the safety net can be retired "
      "(MATCH_SAFETY_NET=false). A person decides.")),
    (["match", "no", "match"], False,
     "Keep the safety net: A alone missed checked labels in 1 of 3 runs."),
])
async def test_retire_only_when_a_misses_no_checked_label_in_every_run(tmp_path, subject_calls, retire, ending):
    def reply(topic, run, b_id):
        if topic.user_text == "AML":
            return subject_calls[run] if b_id == 1 else "no"  # bid 3 is relevant but awaits review
        return "no"  # buyer-topic misses never decide retirement

    matcher = FakeMatcher(reply)
    parts = evaluation_parts(tmp_path, [label(1), label(2, topic="mof"), label(3, to_review=True)], matcher)
    labelled, index, cache, describer, matcher, judge, searcher = parts
    try:
        report = await evaluate(labelled, describer=describer, matcher=matcher, searcher=searcher, judge=judge,
                                index=index, cache=cache, repeats=3)
    finally:
        index.close()

    assert report.retire is retire
    a_subject = [score for score in report.scores if score.way == "A" and score.topics == "subject"]
    assert [score.checked_missed for score in a_subject] == ([0, 0, 0] if retire else [0, 1, 0])
    assert [score.all_missed for score in a_subject] == ([1, 1, 1] if retire else [1, 2, 1])
    assert report_text(report).endswith(ending)
    assert len(set(judge.cache_roots)) == 3  # every repeat starts with a fresh verdict cache


@pytest.mark.parametrize(("repeats", "retire", "ending"), [
    (1, False, "Keep the safety net: A alone missed checked labels in 0 of 1 runs."),
    (2, False, "Keep the safety net: A alone missed checked labels in 0 of 2 runs."),
    (3, True,
     ("A alone missed no checked label in 3 of 3 runs: the safety net can be retired "
      "(MATCH_SAFETY_NET=false). A person decides.")),
])
async def test_retirement_requires_three_zero_miss_subject_runs(tmp_path, repeats, retire, ending):
    matcher = FakeMatcher(lambda topic, run, b_id: "match")
    parts = evaluation_parts(tmp_path, [label(1)], matcher)
    labelled, index, cache, describer, matcher, judge, searcher = parts
    try:
        report = await evaluate(labelled, describer=describer, matcher=matcher, searcher=searcher, judge=judge,
                                index=index, cache=cache, repeats=repeats)
    finally:
        index.close()

    a_subject = [score for score in report.scores if score.way == "A" and score.topics == "subject"]
    assert len(a_subject) == repeats and all(score.checked_missed == 0 for score in a_subject)
    assert report.retire is retire
    assert report_text(report).endswith(ending)


async def test_a_topic_filtered_run_scores_but_never_recommends_global_retirement(tmp_path):
    matcher = FakeMatcher(lambda topic, run, b_id: "match")
    parts = evaluation_parts(tmp_path, [label(1), label(2, topic="mof")], matcher)
    labelled, index, cache, describer, matcher, judge, searcher = parts
    try:
        report = await evaluate(labelled, describer=describer, matcher=matcher, searcher=searcher, judge=judge,
                                index=index, cache=cache, repeats=3, topic="aml")
    finally:
        index.close()

    assert report.retire is False
    assert report.retirement_assessed is False
    assert all(score.checked_missed == 0 for score in report.scores if score.way == "A")
    text = report_text(report)
    assert "Retirement was not assessed" in text
    assert text.endswith("Keep the safety net: A alone missed checked labels in 0 of 3 runs.")


async def test_describe_labelled_reads_only_cached_documents(tmp_path, monkeypatch):
    index = BidIndex(tmp_path / "bids.sqlite")
    cache = DocCache(tmp_path / "docs")
    bid = Bid(b_id=1, bid_number="GEM/2026/B/1", title="Known AML platform", bid_type=1)
    index.upsert([bid], seen_at=NOW)
    first = bid_files(cache, 1, [("bid.pdf", "bid_document", ["AML monitoring for a bank"])])
    second = bid_files(cache, 2, [("bid.pdf", "bid_document", ["Loan origination for a bank"])])
    cache.save(first)
    cache.save(second)
    known = description(1, subject="Existing AML platform", doc_hash_value=doc_hash(first))
    no_documents = description(3, subject="Must not be reused without documents")
    index.put_description(known)
    index.put_description(no_documents)
    labelled = LabelledSet(topics={"aml": AML}, items=[
        label(1, title=bid.title), label(2, title="Lost cache row"), label(3, title="No cached documents"),
    ])
    llm = ScriptedLLM({DESCRIBE_INSTRUCTIONS: [{"subject": "Loan origination platform", "terms": ["LOS"]}]})
    describer = Describer(llm.model(), cache, index, model_name="scripted")

    def no_services(*args, **kwargs):
        raise AssertionError("the match harness must not build GeM services")

    monkeypatch.setattr("gem_agent.app.factory.build_services", no_services)
    try:
        descriptions = await describe_labelled(labelled, describer, index, cache)
        assert index.get(2) is None  # the lost bid was rebuilt from its label, not fetched
    finally:
        index.close()

    assert set(descriptions) == {1, 2}
    assert descriptions[1] == known
    assert descriptions[2].subject == "Loan origination platform"
    assert llm.count(DESCRIBE_INSTRUCTIONS) == 1


async def test_describe_labelled_rewrites_a_description_from_an_old_prompt(tmp_path):
    index = BidIndex(tmp_path / "bids.sqlite")
    cache = DocCache(tmp_path / "docs")
    bid = Bid.from_listing(listing_row(1, NOW, datetime(2026, 11, 28), "Known AML platform"))
    index.upsert([bid], seen_at=NOW)
    files = bid_files(cache, 1, [("bid.pdf", "bid_document", ["AML monitoring for a bank"])])
    cache.save(files)
    index.put_description(description(1, subject="Old prompt description", doc_hash_value=doc_hash(files),
                                      prompt_version="0"))
    labelled = LabelledSet(topics={"aml": AML}, items=[label(1, title=bid.title)])
    llm = ScriptedLLM({DESCRIBE_INSTRUCTIONS: [{"subject": "Current AML platform", "terms": ["AML"]}]})
    describer = Describer(llm.model(), cache, index, model_name="scripted")
    try:
        rows = await describe_labelled(labelled, describer, index, cache)
    finally:
        index.close()

    assert rows[1].subject == "Current AML platform"
    assert rows[1].prompt_version == "1"
    assert llm.count(DESCRIBE_INSTRUCTIONS) == 1


def test_report_text_lists_missed_tenders_with_their_descriptions():
    labelled = LabelledSet(topics={"aml": AML}, items=[label(1)])
    score = score_way("A", {(1, "aml"): False}, labelled, topics="subject", run=1, model_calls=1,
                      descriptions={1: description(1, subject="AML compliance suite")})
    report = MatchReport(scores=[score.model_copy(update={"run": run}) for run in (1, 2, 3)], retire=False)

    text = report_text(report)

    assert text.count("aml:1: AML compliance suite; AML") == 3
    assert text.endswith("Keep the safety net: A alone missed checked labels in 3 of 3 runs.")


async def test_matcher_calls_are_kept_per_topic_for_a_bid_labelled_twice(tmp_path):
    matcher = FakeMatcher(lambda topic, run, b_id: "match" if topic.user_text == "AML" else "no")
    parts = evaluation_parts(tmp_path, [label(1), label(1, topic="mof")], matcher)
    labelled, index, cache, describer, matcher, judge, searcher = parts
    try:
        report = await evaluate(labelled, describer=describer, matcher=matcher, searcher=searcher, judge=judge,
                                index=index, cache=cache, repeats=1)
    finally:
        index.close()

    a_scores = {score.topics: score for score in report.scores if score.way == "A"}
    assert a_scores["subject"].checked_missed == 0
    assert a_scores["buyer"].checked_missed == 1


async def test_today_or_a_uses_todays_read_for_a_safety_net_bid(tmp_path):
    matcher = FakeMatcher(lambda topic, run, b_id: "no")
    parts = evaluation_parts(tmp_path, [label(1)], matcher)
    labelled, index, cache, describer, matcher, judge, searcher = parts
    try:
        report = await evaluate(labelled, describer=describer, matcher=matcher, searcher=searcher, judge=judge,
                                index=index, cache=cache, repeats=1)
    finally:
        index.close()

    by_way = {score.way: score for score in report.scores if score.topics == "subject"}
    assert by_way["today"].checked_missed == 0
    assert by_way["A"].checked_missed == 1
    assert by_way["today or A"].checked_missed == 0
    assert report.retire is False


def test_eval_defaults_to_judge_and_match_accepts_repeats(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path / "data")
    seen = []

    async def fake_eval(given_settings, args):
        assert given_settings is settings
        seen.append((args.what, args.repeats))
        return 0

    monkeypatch.setattr("gem_agent.cli.get_settings", lambda: settings)
    monkeypatch.setattr("gem_agent.cli._eval", fake_eval)

    assert main(["eval"]) == 0
    assert main(["eval", "match", "--repeats", "2"]) == 0
    assert seen == [("judge", 3), ("match", 2)]


def test_eval_match_rejects_zero_repeats(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("gem_agent.cli.get_settings", lambda: Settings(data_dir=tmp_path / "data"))

    with pytest.raises(SystemExit) as exc:
        main(["eval", "match", "--repeats", "0"])

    assert exc.value.code == 2
    assert "at least 1" in capsys.readouterr().err


def test_match_cli_builds_no_gem_services_and_writes_json(tmp_path, monkeypatch, capsys):
    settings = Settings(data_dir=tmp_path / "data")
    labelled_path = tmp_path / "labelled.json"
    LabelledSet(topics={"aml": AML}, items=[label(1)]).save(labelled_path)
    out = tmp_path / "report.json"
    model = FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart("{}")]), model_name="scripted")
    monkeypatch.setattr("gem_agent.cli.get_settings", lambda: settings)
    monkeypatch.setattr("gem_agent.agents.models.chat_model", lambda given_settings: model)

    def no_services(*args, **kwargs):
        raise AssertionError("eval match must not build the live GeM services")

    monkeypatch.setattr("gem_agent.app.factory.build_services", no_services)
    score = WayScore(way="A", topics="subject", run=1, checked_missed=0, all_missed=0, negatives_read=0,
                     negatives=0, model_calls=1, missed=[])
    expected = MatchReport(scores=[score], retire=False, retirement_assessed=False)
    seen, cache_roots = [], []

    async def fake_evaluate(labelled, **kwargs):
        seen.append(kwargs)
        cache_roots.append(kwargs["judge"].verdicts.root.parent)
        assert kwargs["searcher"].live is None
        assert kwargs["repeats"] == 2
        assert kwargs["topic"] == "aml"
        return expected

    monkeypatch.setattr("gem_agent.eval.match.evaluate", fake_evaluate)

    assert main(["eval", "match", "--set", str(labelled_path), "--repeats", "2", "--topic", "aml",
                 "--out", str(out)]) == 0

    assert len(seen) == 1 and seen[0]["topic"] == "aml"
    assert len(cache_roots) == 1 and not cache_roots[0].exists()
    assert json.loads(out.read_text(encoding="utf-8")) == expected.model_dump(mode="json")
    output = capsys.readouterr().out.strip()
    assert "Retirement was not assessed for this topic-filtered run." in output
    assert output.endswith("Keep the safety net: A alone missed checked labels in 0 of 1 runs.")
