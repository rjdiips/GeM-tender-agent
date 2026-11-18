"""bge-reranker-v2-m3."""
from __future__ import annotations

from typing import Protocol


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
