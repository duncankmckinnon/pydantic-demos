from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from chat.agent import MODEL_CHOICES, build_agent
from demo_core.settings import GatewaySettings


def _fixed_reply_model(text: str = "success (no tool calls)") -> FunctionModel:
    # chat_agent always carries WebSearch(), a native tool — TestModel unconditionally rejects
    # any agent with a native tool attached ("TestModel does not support built-in tools"), so
    # FunctionModel (which explicitly supports built-in tools) stands in for it in these tests.
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(text)])

    return FunctionModel(respond)


def test_model_choices_is_non_empty_list_of_pairs() -> None:
    assert len(MODEL_CHOICES) >= 1
    for api_format, model_name in MODEL_CHOICES:
        assert isinstance(api_format, str) and api_format
        assert isinstance(model_name, str) and model_name


def test_build_agent_runs_with_overridden_model() -> None:
    settings = GatewaySettings(api_key="pylf_v1_us_test-key")
    agent = build_agent(settings)

    with agent.override(model=_fixed_reply_model()):
        result = agent.run_sync("hello")

    assert result.output == "success (no tool calls)"
