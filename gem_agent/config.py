"""Settings (pydantic-settings, read from .env): model endpoint, GeM hosts, rate limits, data paths, retention periods."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Model: only the already-loaded model is ever named (spec 3.5)
    llm_base_url: str = "http://10.0.10.11:1234/v1"
    llm_model: str = "qwen/qwen3.6-35b-a3b"
    llm_api_key: str = "not-needed"

    # GeM access (spec 3.2, 6.1, R9)
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

    # Local models (spec 3.5): GPU only when enough is free beside vLLM, else CPU
    embed_device: str = "auto"
    embed_min_free_gpu_mb: int = 3000
    embed_model: str = "BAAI/bge-m3"
    judge_cpu_embed_model: str = "sentence-transformers/all-MiniLM-L6-v2"  # page ranking when the GPU is full
    rerank_model: str = "BAAI/bge-reranker-v2-m3"

    # Local bid index (spec 6.2)
    index_sync_margin_min: float = 30.0
    index_gone_after_missed_refreshes: int = 2

    # Web chat
    org_name: str = ""
    app_host: str = "0.0.0.0"
    app_port: int = 8700

    # Paths
    data_dir: Path = Path("data")
    skills_dir: Path = Path("skills")
    knowledge_dir: Path = Path("knowledge")

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
