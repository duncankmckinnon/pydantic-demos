from collections.abc import Sequence

from pydantic_ai import Agent, AgentCapability
from pydantic_ai.capabilities import WebSearch
from pydantic_ai_harness import Memory, PydanticAIDocs

from demo_core.models import get_model
from demo_core.settings import GatewaySettings

# Update this list to whatever models are enabled on your Gateway project. "openai-responses"
# (not "openai") is required for WebSearch() below: OpenAIChatModel only supports native web
# search on OpenAI's dedicated "-search-preview" model variants, not general models like
# gpt-5.6 — OpenAIResponsesModel supports it for any model.
MODEL_CHOICES: list[tuple[str, str]] = [
    ("anthropic", "claude-sonnet-5"),
    ("openai-responses", "gpt-5.6"),
]


def build_agent(settings: GatewaySettings, *, capabilities: Sequence[AgentCapability] = ()) -> Agent:
    """Build the chat agent using the first entry in MODEL_CHOICES as its default model.

    WebSearch(), Memory(), and PydanticAIDocs() are always on: web search works natively for
    every entry in MODEL_CHOICES (see the comment above), Memory()'s default InMemoryStore is
    scoped to this one Agent instance so it persists across messages within a running process
    without any extra wiring, and PydanticAIDocs() needs no config — it falls back to fetching
    from pydantic/pydantic-ai's main branch on GitHub when no local checkout is configured.
    Additional `capabilities` (e.g. online eval, attached by chat.main) are appended after
    these — kept as a parameter, rather than imported here, so this module stays decoupled from
    chat.evals, which would otherwise import back into this module.
    """
    api_format, model_name = MODEL_CHOICES[0]
    return Agent(
        get_model(api_format, model_name, settings),
        name="chat_agent",
        instructions="You are a helpful, concise assistant.",
        capabilities=[WebSearch(), Memory(), PydanticAIDocs(), *capabilities],
    )
