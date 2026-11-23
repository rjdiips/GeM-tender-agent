"""Page embeddings for the Judge: all-MiniLM-L6-v2 locally (the default), or a remote
OpenAI-compatible server. bge-m3 stays for the eval harness and for an
explicit remote server; the app never loads it into the GPU, which belongs to vLLM."""
from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from typing import Protocol

import httpx
import numpy as np

log = logging.getLogger(__name__)


class Embedder(Protocol):
    model_name: str

    def encode(self, texts: list[str]) -> np.ndarray:
        """L2-normalised float32 vectors, one row per text."""
        ...


def free_gpu_mb() -> int | None:
    """Free memory on GPU 0 from nvidia-smi. Asking torch would open a CUDA context, which itself
    fails (out of memory) while vLLM holds 95% of the card."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout
        return int(out.split()[0])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def pick_device(
    setting: str = "auto", *, min_free_mb: int = 3000, free_gpu_mb: Callable[[], int | None] = free_gpu_mb
) -> str:
    """GPU only when `min_free_mb` is free beside vLLM. The app no longer calls this (it always
    uses `SmallEmbedder` or a remote server); the eval harness uses it to place a
    local bge-m3."""
    if setting != "auto":
        return setting
    free = free_gpu_mb()
    device = "cuda" if free is not None and free >= min_free_mb else "cpu"
    log.info("embedding device: %s (free GPU MB: %s)", device, free)
    return device


class BgeM3Embedder:
    """Dense bge-m3 vectors via sentence-transformers (1024 dims)."""

    def __init__(self, model_name: str = "BAAI/bge-m3", device: str = "cpu", batch_size: int = 64) -> None:
        from sentence_transformers import SentenceTransformer

        self.model_name, self.batch_size = model_name, batch_size
        self._model = SentenceTransformer(model_name, device=device)
        if device == "cuda":
            self._model.half()

    def encode(self, texts: list[str]) -> np.ndarray:
        vectors = self._model.encode(texts, batch_size=self.batch_size, normalize_embeddings=True,
                                     convert_to_numpy=True, show_progress_bar=False)
        return vectors.astype(np.float32)


class SmallEmbedder:
    """all-MiniLM-L6-v2 (22M parameters, English): the Judge's page ranker, in-process on the CPU."""

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2", max_seq_length: int = 256) -> None:
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self._model = SentenceTransformer(model_name, device="cpu")
        self._model.max_seq_length = max_seq_length

    def encode(self, texts: list[str]) -> np.ndarray:
        vectors = self._model.encode(texts, batch_size=32, normalize_embeddings=True, convert_to_numpy=True,
                                     show_progress_bar=False)
        return vectors.astype(np.float32)


class RemoteEmbedder:
    """bge-m3 on another machine, over an OpenAI-compatible `POST {base}/embeddings` (TEI, Infinity, vLLM).

    The base URL includes the API version, e.g. `http://10.0.10.11:8080/v1` for TEI. Every row is
    L2-normalised before it is returned, so callers see the same float32 rows as the local embedders.
    """

    def __init__(self, base_url: str, model_name: str = "BAAI/bge-m3", *, api_key: str = "",
                 timeout_s: float = 60.0, batch_size: int = 64, client: httpx.Client | None = None) -> None:
        self.model_name = model_name
        self.batch_size = batch_size
        self._url = f"{base_url.rstrip('/')}/embeddings"
        self._client = client or httpx.Client(timeout=timeout_s, headers=self._headers(api_key))

    @staticmethod
    def _headers(api_key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {api_key}"} if api_key else {}

    def encode(self, texts: list[str]) -> np.ndarray:
        rows: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            response = self._client.post(self._url, json={"model": self.model_name, "input": batch})
            response.raise_for_status()
            data = sorted(response.json()["data"], key=lambda item: item.get("index", 0))
            if len(data) != len(batch):
                raise ValueError(f"embedding server returned {len(data)} vectors for {len(batch)} inputs")
            rows.extend(item["embedding"] for item in data)
        if not rows:
            return np.zeros((0, 0), dtype=np.float32)
        vectors = np.asarray(rows, dtype=np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        return (vectors / np.maximum(norms, 1e-12)).astype(np.float32)
