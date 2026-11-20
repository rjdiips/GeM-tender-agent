import json

import httpx
import numpy as np
import pytest

from gem_agent.retrieval.embed import RemoteEmbedder
from gem_agent.retrieval.rerank import RemoteReranker


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_remote_embedder_batches_normalises_and_keeps_index_order():
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        # deliberately out of order: the client must follow `index`, not the reply order
        data = [{"index": i, "embedding": [float(i + 1), 1.0]} for i in reversed(range(len(body["input"])))]
        return httpx.Response(200, json={"data": data})

    embedder = RemoteEmbedder("http://gpu:8080/v1", batch_size=2, client=client(handler))
    vectors = embedder.encode(["a", "b", "c"])

    assert [b["input"] for b in bodies] == [["a", "b"], ["c"]]
    assert all(b["model"] == "BAAI/bge-m3" for b in bodies)
    assert vectors.shape == (3, 2) and vectors.dtype == np.float32
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0)
    # row 0 came from index 0 ([1,1]), row 1 from index 1 ([2,1]), not from the reply order
    assert np.allclose(vectors[0], [1 / np.sqrt(2), 1 / np.sqrt(2)])
    assert np.allclose(vectors[1], [2 / np.sqrt(5), 1 / np.sqrt(5)])


def test_remote_embedder_handles_no_texts():
    def handler(request):  # pragma: no cover - must never be called
        raise AssertionError("no request for an empty input")

    assert RemoteEmbedder("http://gpu:8080/v1", client=client(handler)).encode([]).shape == (0, 0)


def test_remote_reranker_tei_posts_texts_and_maps_scores_to_input_order():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=[{"index": 1, "score": 0.9}, {"index": 0, "score": 0.1}])

    reranker = RemoteReranker("http://gpu:8081", style="tei", client=client(handler))

    assert reranker.score("loan system", ["first", "second"]) == [0.1, 0.9]
    assert seen["query"] == "loan system" and seen["texts"] == ["first", "second"]


def test_remote_reranker_cohere_reads_the_results_envelope():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"results": [
            {"index": 1, "relevance_score": 0.75}, {"index": 0, "relevance_score": 0.25},
        ]})

    reranker = RemoteReranker("http://gpu:8081/v1", style="cohere", client=client(handler))

    assert reranker.score("q", ["a", "b"]) == [0.25, 0.75]
    assert seen == {"model": "BAAI/bge-reranker-v2-m3", "query": "q", "documents": ["a", "b"], "top_n": 2}


def test_remote_reranker_rejects_an_incomplete_reply():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"index": 0, "score": 0.5}])

    reranker = RemoteReranker("http://gpu:8081", client=client(handler))
    with pytest.raises(ValueError, match="1 scores for 2 texts"):
        reranker.score("q", ["a", "b"])


def test_remote_reranker_appends_the_path_only_once():
    reranker = RemoteReranker("http://gpu:8081/rerank", client=client(lambda request: httpx.Response(200, json=[])))
    assert reranker._url == "http://gpu:8081/rerank"
