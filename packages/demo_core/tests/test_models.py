import pytest
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel

from demo_core.models import get_model
from demo_core.settings import GatewaySettings


def test_get_model_openai_returns_openai_chat_model() -> None:
    settings = GatewaySettings(api_key="pylf_v1_us_test-key")
    model = get_model("openai", "gpt-5.2", settings)
    assert isinstance(model, OpenAIChatModel)


def test_get_model_anthropic_returns_anthropic_model() -> None:
    settings = GatewaySettings(api_key="pylf_v1_us_test-key")
    model = get_model("anthropic", "claude-sonnet-4-6", settings)
    assert isinstance(model, AnthropicModel)


def test_get_model_rejects_unsupported_format() -> None:
    settings = GatewaySettings(api_key="pylf_v1_us_test-key")
    with pytest.raises(ValueError, match="Unsupported api_format"):
        get_model("cohere", "command", settings)
