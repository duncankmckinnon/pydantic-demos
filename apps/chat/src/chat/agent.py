from collections.abc import Sequence

from pydantic_ai import Agent, AgentCapability

from demo_core.models import get_model
from demo_core.settings import GatewaySettings

# Update this list to whatever models are enabled on your Gateway project.
MODEL_CHOICES: list[tuple[str, str]] = [
    ("anthropic", "claude-sonnet-4-6"),
    ("openai", "gpt-5.2"),
]


def build_agent(settings: GatewaySettings, *, capabilities: Sequence[AgentCapability] = ()) -> Agent:
    """Build the chat agent using the first entry in MODEL_CHOICES as its default model.

    `capabilities` defaults to none so offline eval runs (chat.evals.run) stay plain; callers
    that want online evaluation attached (chat.main) pass it explicitly — keeps this module
    decoupled from chat.evals, which would otherwise import back into this module.
    """
    api_format, model_name = MODEL_CHOICES[0]
    return Agent(
        get_model(api_format, model_name, settings),
        name="chat_agent",
        instructions="You are a helpful, concise assistant.",
        capabilities=list(capabilities),
    )
