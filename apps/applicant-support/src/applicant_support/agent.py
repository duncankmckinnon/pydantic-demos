from dataclasses import dataclass
from datetime import datetime

from collections.abc import Sequence

from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities import AgentCapability

from applicant_support.config import ALLOWED_MODELS, DEFAULT_MODEL
from applicant_support.db import ApplicantDatabase, ApplicationRecord, InterviewRecord, RoleRecord
from demo_core.models import get_model
from demo_core.settings import GatewaySettings

MODEL_CHOICES: list[tuple[str, str]] = [DEFAULT_MODEL.model_choice] + sorted(
    ALLOWED_MODELS - {DEFAULT_MODEL.model_choice}
)


@dataclass
class AgentDeps:
    database: ApplicantDatabase
    application_id: str


def build_agent(
    settings: GatewaySettings,
    *,
    capabilities: Sequence[AgentCapability[AgentDeps]] = (),
) -> Agent[AgentDeps, str]:
    api_format, model_name = DEFAULT_MODEL.model_choice
    agent = Agent(
        get_model(api_format, model_name, settings),
        name="applicant_support_agent",
        deps_type=AgentDeps,
        capabilities=capabilities,
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
