"""Settings (pydantic-settings, read from .env): model endpoint, GeM hosts, rate limits, data paths, retention periods."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

RerankStyle = Literal["tei", "cohere"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Model: only the already-loaded model is ever named
    llm_base_url: str = "http://10.0.10.11:1234/v1"
    llm_model: str = "qwen/qwen3.6-35b-a3b"
    llm_api_key: str = "not-needed"

    # GeM access
    gem_base_url: str = "https://bidplus.gem.gov.in"
    allowed_host_suffix: str = "gem.gov.in"
    rate_limit_floor_s: float = 1.0
    rate_limit_error_s: float = 4.0
    rate_limit_recover_min: float = 10.0
    rate_limit_spike_median_s: float = 2.0
    http_retries: int = 3
    http_backoff_s: float = 2.0
    http_timeout_s: float = 60.0
    user_agent: str = "Mozilla/5.0"
    daily_request_budget: int = 20000

    # Local models: the Judge ranks pages with all-MiniLM-L6-v2 unless a remote
    # embedder URL is set. bge-m3 is kept for the eval harness only; the GPU belongs to vLLM.
    embed_model: str = "sentence-transformers/all-MiniLM-L6-v2"  # the id a remote embedder serves
    judge_cpu_embed_model: str = "sentence-transformers/all-MiniLM-L6-v2"  # the local default
    rerank_model: str = "BAAI/bge-reranker-v2-m3"

    # Remote models: run embeddings and/or the reranker on another machine instead of this one.
    # Empty URL = local (the behaviour above). The embed base includes the API version, e.g. .../v1.
    embed_base_url: str = ""
    embed_api_key: str = ""
    embed_timeout_s: float = 30.0
    embed_batch_size: int = 64
    rerank_base_url: str = ""
    rerank_style: RerankStyle = "tei"
    rerank_api_key: str = ""
    rerank_timeout_s: float = 30.0

    # Description matching: the safety net is on unless a person turns it off.
    match_safety_net: bool = True
    describe_cap: int = 300  # new descriptions per request, shared across all topics and both stages

    # Windows: full path to tesseract.exe when it is not on PATH (e.g. C:\Program Files\Tesseract-OCR\tesseract.exe)
    tesseract_cmd: str = ""

    # Web chat
    org_name: str = ""
    app_host: str = "0.0.0.0"
    app_port: int = 8700

    # Paths
    data_dir: Path = Path("data")
    skills_dir: Path = Path("skills")
    knowledge_dir: Path = Path("knowledge")

    # Retention: days after a bid closes before its cached files are deleted (jobs/retention.py)
    retain_quarantine_days: int = 30  # documents of a bid no run found relevant
    retain_kept_after_close_days: int = 180  # documents of a bid a run found relevant
    retain_verdict_after_close_days: int = 30  # verdicts, for every bid

    @property
    def rate_limit_dir(self) -> Path:
        """Per-host limiter state shared by every process on this machine."""
        return self.data_dir / "ratelimit"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def index_path(self) -> Path:
        return self.data_dir / "index" / "bids.sqlite"

    @property
    def bid_cache_path(self) -> Path:
        return self.data_dir / "bids" / "bids.sqlite"


@lru_cache
def get_settings() -> Settings:
    return Settings()
