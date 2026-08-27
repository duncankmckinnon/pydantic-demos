from dataclasses import dataclass

from pydantic_ai import Agent, RunContext

from demo_core.models import get_model
from demo_core.settings import GatewaySettings
from rx_assistant.db import ConditionMatch, MedicationMatch, query_conditions, query_medications
from rx_assistant.embeddings import encode_text

# Update this list to whatever models are enabled on your Gateway project.
MODEL_CHOICES: list[tuple[str, str]] = [
    ("anthropic", "claude-sonnet-4-6"),
    ("openai", "gpt-5.2"),
]


@dataclass
class Deps:
    pool: object
    embedding_model: object


def build_agent(settings: GatewaySettings) -> Agent[Deps, str]:
    """Build the rx-assistant agent using the first entry in MODEL_CHOICES as its default
    model. Deps (a real asyncpg pool + loaded embedding model, or test doubles) must be
    passed to every agent.run(...) call."""
    api_format, model_name = MODEL_CHOICES[0]
    agent = Agent(
        get_model(api_format, model_name, settings),
        name="rx_assistant_agent",
        deps_type=Deps,
        instructions=(
            "You are a medical information assistant over a demo medications and "
            "conditions database. Use the search_conditions and search_medications "
            "tools before answering any question about a condition or medication. Cite "
            "the specific medication names, prices, and manufacturers you retrieved. "
            "This is demo data scraped from a public retail site, not medical advice — "
            "always tell the user to consult a healthcare professional for real decisions."
        ),
    )

    @agent.tool
    async def search_conditions(
        ctx: RunContext[Deps], query: str, limit: int = 5
    ) -> list[ConditionMatch]:
        """Find conditions/diseases in the database matching a natural-language query."""
        embedding = encode_text(ctx.deps.embedding_model, query)
        return await query_conditions(ctx.deps.pool, embedding, limit)

    @agent.tool
    async def search_medications(
        ctx: RunContext[Deps], query: str, condition: str | None = None, limit: int = 5
    ) -> list[MedicationMatch]:
        """Find medications matching a natural-language query, optionally scoped to a
        condition name (e.g. one returned by search_conditions)."""
        embedding = encode_text(ctx.deps.embedding_model, query)
        return await query_medications(ctx.deps.pool, embedding, condition, limit)

    return agent
