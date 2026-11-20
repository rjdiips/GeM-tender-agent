"""bge-reranker-v2-m3: local cross-encoder, or a remote `/rerank` server (TEI, Infinity)."""
from __future__ import annotations

from typing import Protocol

import httpx

from gem_agent.config import RerankStyle


class Reranker(Protocol):
    def score(self, query: str, texts: list[str]) -> list[float]:
        """Relevance of each text to the query; higher is better."""
        ...


class BgeReranker:
    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3", device: str = "cpu", batch_size: int = 32) -> None:
        from sentence_transformers import CrossEncoder

        self.batch_size = batch_size
        self._model = CrossEncoder(model_name, device=device)

    def score(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        scores = self._model.predict([(query, t) for t in texts], batch_size=self.batch_size, show_progress_bar=False)
        return [float(s) for s in scores]


class RemoteReranker:
    """bge-reranker-v2-m3 on another machine.

    `style="tei"` posts `{query, texts}` to the native endpoint; `style="cohere"` posts
    `{model, query, documents, top_n}` to a Jina/Cohere-compatible one. Both replies are accepted, so a TEI
    image that serves `/v1/rerank` works either way. `base_url` may be the API base (the client appends
    `/rerank`) or the full endpoint. Scores are returned in the order of `texts`; an incomplete reply is an
    error, not a silent reordering.
    """

    def __init__(self, base_url: str, model_name: str = "BAAI/bge-reranker-v2-m3", *, style: RerankStyle = "tei",
                 api_key: str = "", timeout_s: float = 60.0, client: httpx.Client | None = None) -> None:
        self.model_name = model_name
        self.style = style
        self._url = base_url if base_url.rstrip("/").endswith("/rerank") else f"{base_url.rstrip('/')}/rerank"
        self._client = client or httpx.Client(timeout=timeout_s, headers=self._headers(api_key))

    @staticmethod
    def _headers(api_key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {api_key}"} if api_key else {}

    def _body(self, query: str, texts: list[str]) -> dict:
        if self.style == "tei":
            return {"query": query, "texts": texts}
        return {"model": self.model_name, "query": query, "documents": texts, "top_n": len(texts)}

    def score(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        response = self._client.post(self._url, json=self._body(query, texts))
        response.raise_for_status()
        payload = response.json()
        results = payload["results"] if isinstance(payload, dict) else payload
        scores = [0.0] * len(texts)
        seen: set[int] = set()
        for item in results:
            index = int(item["index"])
            if not 0 <= index < len(scores):
                raise ValueError(f"rerank server returned index {index} for {len(texts)} texts")
            if index in seen:
                raise ValueError(f"rerank server returned index {index} twice")
            seen.add(index)
            scores[index] = float(item.get("relevance_score", item.get("score", 0.0)))
        if len(seen) != len(texts):
            raise ValueError(f"rerank server returned {len(seen)} scores for {len(texts)} texts")
        return scores
