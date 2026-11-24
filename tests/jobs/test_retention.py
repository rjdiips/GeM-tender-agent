import json
from datetime import datetime, timedelta

import pytest

from gem_agent.agents.schemas import Verdict
from gem_agent.config import Settings
from gem_agent.graph.store import Outcome, RunRecord, RunStore
from gem_agent.jobs.retention import prune_caches
from gem_agent.tools.gem.models import Bid

NOW = datetime(2026, 11, 24, 12)


@pytest.fixture(autouse=True)
def no_labelled_set(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "eval").mkdir()
    (tmp_path / "eval" / "labelled_set.json").write_text(json.dumps({"items": []}), encoding="utf-8")


def settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


def outcome(b_id, closing, relevant) -> Outcome:
    bid = Bid(b_id=b_id, bid_number=f"GEM/2026/B/{b_id}", title="LOS for RRBs", bid_type=1, closing=closing)
    verdict = Verdict(b_id=b_id, bid_number=bid.bid_number, topic_key="t", doc_hash="d", relevant=relevant,
                      confidence=0.9, stage="judge", triage="maybe", reason="r", model="m", decided_at=NOW)
    return Outcome(b_id=b_id, group="g", bid=bid, verdict=verdict)


def add_run(cfg: Settings, run_id: str, outcomes: list[Outcome], status: str = "complete") -> None:
    store = RunStore(cfg.runs_dir)
    store.create(RunRecord(run_id=run_id, user="u", title="t", request_text="x", created=NOW, status=status))
    for o in outcomes:
        store.save_outcome(run_id, o)


def add_shortlist(cfg: Settings, run_id: str, b_ids: list[int]) -> None:
    run_dir = cfg.runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "shortlist.json").write_text(json.dumps([{"b_id": b} for b in b_ids]), encoding="utf-8")


def add_cache(cfg: Settings, b_id: int) -> tuple:
    docs = cfg.cache_dir / "docs" / str(b_id)
    (docs / "files").mkdir(parents=True)
    (docs / "files" / "a.pdf").write_bytes(b"x" * 100)
    (docs / "manifest.json").write_text("{}", encoding="utf-8")
    verdicts = cfg.cache_dir / "verdicts" / str(b_id)
    verdicts.mkdir(parents=True)
    (verdicts / "t-d.json").write_text("{}", encoding="utf-8")
    return docs, verdicts


def test_quarantined_bid_docs_removed_30_days_after_close(tmp_path):
    cfg = settings(tmp_path)
    add_run(cfg, "r1", [outcome(1, NOW - timedelta(days=31), relevant=False),
                        outcome(2, NOW - timedelta(days=10), relevant=False)])
    docs1, _ = add_cache(cfg, 1)
    docs2, _ = add_cache(cfg, 2)
    orphan_docs, _ = add_cache(cfg, 99)

    prune_caches(cfg, NOW, dry_run=False)

    assert not docs1.exists()
    assert docs2.exists()
    assert orphan_docs.exists()


def test_relevant_bid_docs_kept_until_180_days(tmp_path):
    cfg = settings(tmp_path)
    add_run(cfg, "r1", [outcome(1, NOW - timedelta(days=100), relevant=True)])
    add_run(cfg, "r2", [outcome(1, NOW - timedelta(days=100), relevant=False)])
    docs, _ = add_cache(cfg, 1)

    prune_caches(cfg, NOW, dry_run=False)

    assert docs.exists()


def test_verdicts_removed_30_days_after_close(tmp_path):
    cfg = settings(tmp_path)
    add_run(cfg, "r1", [outcome(1, NOW - timedelta(days=31), relevant=True),
                        outcome(2, NOW - timedelta(days=29), relevant=False)])
    _, verdicts1 = add_cache(cfg, 1)
    _, verdicts2 = add_cache(cfg, 2)

    prune_caches(cfg, NOW, dry_run=False)

    assert not verdicts1.exists()
    assert verdicts2.exists()


def test_bid_in_running_run_is_kept(tmp_path):
    cfg = settings(tmp_path)
    add_run(cfg, "r1", [outcome(1, NOW - timedelta(days=60), relevant=False)], status="running")
    docs, verdicts = add_cache(cfg, 1)

    prune_caches(cfg, NOW, dry_run=False)

    assert docs.exists() and verdicts.exists()


def test_shortlisted_bid_in_running_run_is_kept_with_no_outcome(tmp_path):
    cfg = settings(tmp_path)
    add_run(cfg, "finished", [outcome(1, NOW - timedelta(days=31), relevant=False)])
    add_run(cfg, "running", [], status="running")
    add_shortlist(cfg, "running", [1])  # shortlisted there, not yet judged there
    docs, verdicts = add_cache(cfg, 1)

    prune_caches(cfg, NOW, dry_run=False)

    assert docs.exists() and verdicts.exists()


def test_unreadable_shortlist_does_not_stop_the_pass(tmp_path):
    cfg = settings(tmp_path)
    add_run(cfg, "finished", [outcome(1, NOW - timedelta(days=31), relevant=False)])
    add_run(cfg, "running", [], status="running")
    (cfg.runs_dir / "running" / "shortlist.json").write_text("{not json", encoding="utf-8")
    docs, _ = add_cache(cfg, 1)

    prune_caches(cfg, NOW, dry_run=False)

    assert not docs.exists()  # no ids could be read, so only the finished run's bid counts


def test_labelled_set_bid_is_kept(tmp_path):
    cfg = settings(tmp_path)
    (tmp_path / "eval" / "labelled_set.json").write_text(json.dumps({"items": [{"b_id": 1}]}), encoding="utf-8")
    add_run(cfg, "r1", [outcome(1, NOW - timedelta(days=60), relevant=False)])
    docs, verdicts = add_cache(cfg, 1)

    prune_caches(cfg, NOW, dry_run=False)

    assert docs.exists() and verdicts.exists()


def test_dry_run_deletes_nothing(tmp_path):
    cfg = settings(tmp_path)
    add_run(cfg, "r1", [outcome(1, NOW - timedelta(days=60), relevant=False)])
    docs, verdicts = add_cache(cfg, 1)

    report = prune_caches(cfg, NOW, dry_run=True)

    assert docs.exists() and verdicts.exists()
    assert report.bids == 1 and report.files == 3 and report.bytes == 100 + 2 + 2


def test_max_bids_caps_one_pass(tmp_path):
    cfg = settings(tmp_path)
    add_run(cfg, "r1", [outcome(b, NOW - timedelta(days=60), relevant=False) for b in range(1, 6)])
    for b in range(1, 6):
        add_cache(cfg, b)

    report = prune_caches(cfg, NOW, dry_run=False, max_bids=2)

    assert report.bids == 2
    assert sum((cfg.cache_dir / "docs" / str(b)).exists() for b in range(1, 6)) == 3


def test_gtc_cache_untouched(tmp_path):
    cfg = settings(tmp_path)
    add_run(cfg, "r1", [outcome(1, NOW - timedelta(days=60), relevant=False)])
    add_cache(cfg, 1)
    gtc = cfg.cache_dir / "gtc" / "20261014.pdf"
    gtc.parent.mkdir(parents=True)
    gtc.write_bytes(b"%PDF terms")

    prune_caches(cfg, NOW, dry_run=False)

    assert gtc.read_bytes() == b"%PDF terms"


def test_unreadable_labelled_set_stops_the_pass_before_any_deletion(tmp_path):
    cfg = settings(tmp_path)
    (tmp_path / "eval" / "labelled_set.json").write_text("{not json", encoding="utf-8")
    add_run(cfg, "r1", [outcome(1, NOW - timedelta(days=60), relevant=False)])
    docs, _ = add_cache(cfg, 1)

    with pytest.raises(ValueError):
        prune_caches(cfg, NOW, dry_run=False)

    assert docs.exists()
