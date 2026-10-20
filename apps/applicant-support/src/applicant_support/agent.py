from dataclasses import dataclass
from datetime import datetime

from pydantic_ai import Agent, RunContext

from applicant_support.db import ApplicantDatabase, ApplicationRecord, InterviewRecord, RoleRecord
from demo_core.models import get_model
from demo_core.settings import GatewaySettings

MODEL_CHOICES: list[tuple[str, str]] = [
    ("anthropic", "claude-sonnet-5"),
    ("openai-responses", "gpt-5.6"),
]


@dataclass
class AgentDeps:
    database: ApplicantDatabase
    application_id: str


def build_agent(settings: GatewaySettings) -> Agent[AgentDeps, str]:
    api_format, model_name = MODEL_CHOICES[0]
    agent = Agent(
        get_model(api_format, model_name, settings),
        name="applicant_support_agent",
        deps_type=AgentDeps,
        instructions=(
            "You are an HR representative. Answer the applicant's questions and use "
            "the available tools when helpful."
        ),
    )

    @agent.tool
    def get_application_status(ctx: RunContext[AgentDeps]) -> ApplicationRecord | None:
        """Look up the current applicant's application and interview details."""
        return ctx.deps.database.get_application(ctx.deps.application_id)

    @agent.tool
    def reschedule_screening_call(
        ctx: RunContext[AgentDeps], starts_at: datetime
    ) -> InterviewRecord | None:
        """Move the current applicant's existing screening call to an ISO date and time."""
        return ctx.deps.database.reschedule_interview(ctx.deps.application_id, starts_at)

    @agent.tool
    def search_roles(ctx: RunContext[AgentDeps], query: str = "") -> list[RoleRecord]:
        """Search job roles by words in the role title, including open and closed roles."""
        return ctx.deps.database.search_roles(query)

    return agent
