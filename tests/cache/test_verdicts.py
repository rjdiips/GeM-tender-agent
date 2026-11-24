import threading
from datetime import datetime

from gem_agent.agents.schemas import Verdict
from gem_agent.cache.verdicts import VerdictCache


def verdict(b_id: int = 7) -> Verdict:
    return Verdict(b_id=b_id, bid_number=f"GEM/2026/B/{b_id}", topic_key="topic1", doc_hash="doc1", relevant=True,
                   confidence=0.9, stage="judge", triage="maybe", reason="scope", model="m",
                   decided_at=datetime(2026, 11, 24, 12))


def test_verdict_round_trip(tmp_path):
    v = verdict()
    VerdictCache(tmp_path, "aaaa").put(v)
    assert VerdictCache(tmp_path, "aaaa").get(v.b_id, v.topic_key, v.doc_hash) == v


def test_other_version_is_a_miss(tmp_path):
    v = verdict()
    VerdictCache(tmp_path, "aaaa").put(v)
    assert VerdictCache(tmp_path, "bbbb").get(v.b_id, v.topic_key, v.doc_hash) is None


def test_corrupt_verdict_is_a_miss(tmp_path):
    cache = VerdictCache(tmp_path)
    v = verdict()
    cache.put(v)
    (path,) = (tmp_path / "verdicts" / str(v.b_id)).glob("*.json")
    path.write_text("{not json")
    assert cache.get(v.b_id, v.topic_key, v.doc_hash) is None


def test_concurrent_puts_do_not_collide(tmp_path):
    cache = VerdictCache(tmp_path)
    v = verdict()
    errors: list[OSError] = []
    start = threading.Barrier(2)

    def writer() -> None:
        start.wait()
        try:
            for _ in range(50):
                cache.put(v)
        except OSError as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert cache.get(v.b_id, v.topic_key, v.doc_hash) == v
