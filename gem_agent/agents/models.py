"""Shared model client: only the already-loaded model is ever named (spec 3.5)."""
from __future__ import annotations

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from gem_agent.config import Settings, get_settings


def chat_model(settings: Settings | None = None) -> OpenAIChatModel:
    """vLLM's OpenAI-compatible endpoint. Checked 16 Nov: no hidden reasoning tokens, and `response_format`
    JSON schemas are enforced, so agents use NativeOutput."""
    s = settings or get_settings()
    provider = OpenAIProvider(base_url=s.llm_base_url, api_key=s.llm_api_key)
    # Qwen's chat template refuses a system message anywhere but first ("System message must be at the beginning",
    # 400). pydantic-ai adds one mid-conversation when it sends a malformed answer back for another try, so every
    # such retry failed (2 bids in the 16 Nov re-judge). Off: it goes as a <system> block in a user turn instead.
    profile = OpenAIChatModel(s.llm_model, provider=provider).profile
    return OpenAIChatModel(s.llm_model, provider=provider, profile={**profile, "supports_inline_system_prompts": False,
                                                                         "default_framing_announcement": "never"})
