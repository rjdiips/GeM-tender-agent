import json
from datetime import datetime, timedelta

from gem_agent.app.factory import judge_embedder, judge_reranker, labelled_b_ids, startup_prune
from gem_agent.config import Settings
from gem_agent.index.db import BidIndex
from gem_agent.retrieval.embed import RemoteEmbedder
from gem_agent.retrieval.rerank import RemoteReranker
from gem_agent.tools.gem.models import Bid
from tests.index.helpers import row

NOW = datetime(2026, 12, 30)


def test_labelled_bids_are_read_without_validating_the_set(tmp_path):
    path = tmp_path / "labelled_set.json"
    # a bucket value this branch's loader does not know: the ids are still read
    path.write_text(json.dumps({"topics": {}, "items": [{"b_id": 7, "bucket": "new-kind"}, {"b_id": 9}]}))
    assert labelled_b_ids(path) == {7, 9}
    assert labelled_b_ids(tmp_path / "missing.json") == set()


def test_the_startup_prune_keeps_the_labelled_bids(tmp_path):
    index = BidIndex(":memory:")
    closed = NOW - timedelta(days=40)
    index.upsert([Bid.from_listing(row(i, closed - timedelta(days=10), closed)) for i in (1, 2)], seen_at=closed)
    path = tmp_path / "labelled_set.json"
    path.write_text(json.dumps({"items": [{"b_id": 2}]}))
    assert startup_prune(index, path, NOW) == 1
    assert index.get(1) is None and index.get(2) is not None
    index.close()


def test_remote_urls_select_the_remote_clients():
    settings = Settings(embed_base_url="http://gpu:8080/v1", rerank_base_url="http://gpu:8081")
    assert isinstance(judge_embedder(settings), RemoteEmbedder)
    assert isinstance(judge_reranker(settings), RemoteReranker)


def test_without_an_embed_url_the_judge_uses_minilm(monkeypatch):
    from gem_agent.app import factory

    class FakeMiniLM:
        def __init__(self, model_name):
            self.model_name = model_name
            self.seen = model_name

    monkeypatch.setattr(factory, "SmallEmbedder", FakeMiniLM)
    embedder = factory.judge_embedder(Settings(embed_base_url=""))
    assert isinstance(embedder, FakeMiniLM)
    assert embedder.model_name == Settings().judge_cpu_embed_model


def test_without_a_rerank_url_reranking_stays_off():
    assert judge_reranker(Settings(rerank_base_url="")) is None
