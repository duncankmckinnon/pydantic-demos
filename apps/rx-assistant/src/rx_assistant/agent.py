from collections.abc import Sequence
from dataclasses import dataclass

from pydantic_ai import Agent, AgentCapability, RunContext
from pydantic_ai_harness.subagents import SubAgent, SubAgents

from demo_core.models import get_model
from demo_core.settings import GatewaySettings
from rx_assistant.db import MedicationMatch, query_medications
from rx_assistant.embeddings import encode_text
from rx_assistant.web_research import build_web_research_agent

# Update this list to whatever models are enabled on your Gateway project.
MODEL_CHOICES: list[tuple[str, str]] = [
    ("anthropic", "claude-sonnet-4-6"),
    ("openai", "gpt-5.2"),
]


@dataclass
class Deps:
    pool: object
    embedding_model: object


def build_agent(
    settings: GatewaySettings, *, capabilities: Sequence[AgentCapability] = ()
) -> Agent[Deps, str]:
    """Build the rx-assistant agent using the first entry in MODEL_CHOICES as its default
    model. Deps (a real asyncpg pool + loaded embedding model, or test doubles) must be
    passed to every agent.run(...) call. Additional `capabilities` (e.g. the online eval,
    attached by rx_assistant.main) are appended as-is — kept as a parameter, rather than
    imported here, so this module stays decoupled from rx_assistant.evals, which would
    otherwise import back into this module."""
    api_format, model_name = MODEL_CHOICES[0]
    web_research_agent = build_web_research_agent(settings, MODEL_CHOICES[0])
    agent = Agent(
        get_model(api_format, model_name, settings),
        name="rx_assistant_agent",
        deps_type=Deps,
        capabilities=[
            SubAgents(
                agents=[
                    SubAgent(
                        web_research_agent,
                        name="web_research",
                        description=(
                            "Fetch and summarize one known source when the database and "
                            "your own knowledge aren't enough: for a medication, pass its "
                            "name and med_url (from search_medications); for a condition, "
                            "pass just its name. It reads only that one source; it cannot "
                            "search the web more broadly."
                        ),
                    )
                ]
            ),
            *capabilities,
        ],
        instructions=(
            "You are a pharmacy prescription assistant. Patients will ask you questions "
            "about their medications and conditions. Use your own judgment about when to "
            "look something up. When you do reference a specific medication, cite its "
            "name, generic name, and manufacturer. Tell the user to consult a healthcare "
            "professional or pharmacist for real decisions if it seems like they are "
            "treating this as medical advice."
        ),
    )

    @agent.tool
    async def search_medications(
        ctx: RunContext[Deps], query: str, condition: str | None = None, limit: int = 5
    ) -> list[MedicationMatch]:
        """Find medications matching a natural-language query, optionally scoped to a
        condition name."""
        embedding = await encode_text(ctx.deps.embedding_model, query)
        return await query_medications(ctx.deps.pool, embedding, condition, limit)

    return agent
