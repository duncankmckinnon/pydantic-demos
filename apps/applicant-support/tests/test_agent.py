from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from applicant_support.agent import AgentDeps, MODEL_CHOICES, build_agent
from applicant_support.db import ApplicantDatabase
from demo_core.settings import GatewaySettings


def test_model_choices_are_non_empty_pairs() -> None:
    assert MODEL_CHOICES
    assert all(api_format and model_name for api_format, model_name in MODEL_CHOICES)


def test_agent_can_look_up_current_application(tmp_path) -> None:
    database = ApplicantDatabase(tmp_path / "applicants.sqlite3")
    database.initialize()
    agent = build_agent(GatewaySettings(api_key="pylf_v1_us_test-key"))

    with agent.override(model=TestModel(call_tools=["get_application_status"])):
        result = agent.run_sync(
            "Did I pass screening?",
            deps=AgentDeps(database=database, application_id="APP-1001"),
        )

    assert "Maya Chen" in result.output
    assert "screening_passed" in result.output


def test_agent_can_search_roles(tmp_path) -> None:
    database = ApplicantDatabase(tmp_path / "applicants.sqlite3")
    database.initialize()
    agent = build_agent(GatewaySettings(api_key="pylf_v1_us_test-key"))

    with agent.override(model=TestModel(call_tools=["search_roles"])):
        result = agent.run_sync(
            "Is another role open?",
            deps=AgentDeps(database=database, application_id="APP-1001"),
        )

    assert "Customer Support Specialist" in result.output
    assert "is_open" in result.output


def test_agent_can_reschedule_existing_screening_call(tmp_path) -> None:
    database = ApplicantDatabase(tmp_path / "applicants.sqlite3")
    database.initialize()
    agent = build_agent(GatewaySettings(api_key="pylf_v1_us_test-key"))

    def reschedule_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tool_has_returned = any(
            isinstance(part, ToolReturnPart) for message in messages for part in message.parts
        )
        if tool_has_returned:
            return ModelResponse(parts=[TextPart("Your call was rescheduled.")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "reschedule_screening_call",
                    {"starts_at": "2026-10-27T14:30:00-07:00"},
                )
            ]
        )

    with agent.override(model=FunctionModel(reschedule_model)):
        result = agent.run_sync(
            "Please reschedule my call.",
            deps=AgentDeps(database=database, application_id="APP-1001"),
        )

    assert result.output == "Your call was rescheduled."
    assert database.get_application("APP-1001").interview_starts_at.isoformat() == (
        "2026-10-27T14:30:00-07:00"
    )
