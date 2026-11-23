"""The shared model client: Qwen's chat template accepts one system message, and only first."""
from gem_agent.agents.models import chat_model
from gem_agent.config import Settings


def test_no_system_message_is_sent_mid_conversation_or_as_a_second_leading_one():
    # every retry after a malformed answer failed with 400 "System message must be at the beginning"
    profile = chat_model(Settings()).profile
    assert profile["supports_inline_system_prompts"] is False
    assert profile["default_framing_announcement"] == "never"
