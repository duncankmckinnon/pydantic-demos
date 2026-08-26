from pydantic_ai.models.test import TestModel

from chat.agent import MODEL_CHOICES, build_agent
from demo_core.settings import GatewaySettings


def test_model_choices_is_non_empty_list_of_pairs() -> None:
    assert len(MODEL_CHOICES) >= 1
    for api_format, model_name in MODEL_CHOICES:
        assert isinstance(api_format, str) and api_format
        assert isinstance(model_name, str) and model_name


def test_build_agent_runs_with_overridden_model() -> None:
    settings = GatewaySettings(api_key="pylf_v1_us_test-key")
    agent = build_agent(settings)

    with agent.override(model=TestModel()):
        result = agent.run_sync("hello")

    assert result.output == "success (no tool calls)"
