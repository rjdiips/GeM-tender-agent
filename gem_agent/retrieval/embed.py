"""bge-m3 embeddings, on the GPU only when vLLM leaves room (spec 3.5)."""
from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from typing import Protocol

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
    """all-MiniLM-L6-v2 (22M parameters, English) for ranking a bid's own pages on the CPU, when vLLM leaves the GPU
    no room for bge-m3. Checked 17 Nov on 18 relevant bids: the page the verdict quoted was in the top 8 for 12 with
    words + MiniLM, 10 with words + bge-m3 on the CPU (423 s), 8 with words alone; MiniLM took 6 s for all 18."""

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2", max_seq_length: int = 256) -> None:
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self._model = SentenceTransformer(model_name, device="cpu")
        self._model.max_seq_length = max_seq_length

    def encode(self, texts: list[str]) -> np.ndarray:
        vectors = self._model.encode(texts, batch_size=32, normalize_embeddings=True, convert_to_numpy=True,
                                     show_progress_bar=False)
        return vectors.astype(np.float32)
