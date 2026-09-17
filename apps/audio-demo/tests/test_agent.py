from pydantic_ai.models.test import TestModel

from audio_demo.agent import build_agent
from demo_core.models import get_realtime_model
from demo_core.settings import GatewaySettings


async def test_sample_tool_is_available_to_the_agent():
    agent = build_agent()
    with agent.override(model=TestModel()):
        result = await agent.run("Look up my payments")
    assert "$412.18" in result.output
    assert "Autopay is enabled" in result.output


def test_realtime_model_uses_this_apps_gateway():
    model = get_realtime_model("gpt-realtime", GatewaySettings())
    assert model.model_name == "gpt-realtime"
    assert "gateway" in str(model.base_url)
    assert str(model.base_url).rstrip("/").endswith("/openai")
