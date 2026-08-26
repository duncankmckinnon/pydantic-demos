from pydantic_ai import Agent

from demo_core.models import get_model
from demo_core.settings import GatewaySettings

# Update this list to whatever models are enabled on your Gateway project.
MODEL_CHOICES: list[tuple[str, str]] = [
    ("anthropic", "claude-sonnet-4-6"),
    ("openai", "gpt-5.2"),
]


def build_agent(settings: GatewaySettings) -> Agent:
    """Build the chat agent using the first entry in MODEL_CHOICES as its default model."""
    api_format, model_name = MODEL_CHOICES[0]
    return Agent(
        get_model(api_format, model_name, settings),
        name="chat_agent",
        instructions="You are a helpful, concise assistant.",
    )
