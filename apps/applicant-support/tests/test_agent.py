from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from applicant_support.agent import AgentDeps, MODEL_CHOICES, build_agent
from applicant_support.config import AgentConfig, ResolvedAgentConfig
from applicant_support.db import ApplicantDatabase
from applicant_support.service import run_applicant_turn
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


async def test_shared_runner_applies_config_history_and_safe_metadata(
    monkeypatch, tmp_path
) -> None:
    database = ApplicantDatabase(tmp_path / "applicants.sqlite3")
    database.initialize()
    agent = build_agent(GatewaySettings(api_key="pylf_v1_us_test-key"))
    seen: dict[str, object] = {}

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen["messages"] = messages
        seen["instructions"] = info.instructions
        return ModelResponse(parts=[TextPart("Configured reply")])

    original_run = agent.run

    async def recording_run(*args, **kwargs):
        seen["run_kwargs"] = kwargs
        return await original_run(*args, **kwargs)

    monkeypatch.setattr(agent, "run", recording_run)
    prompt = "Use the application lookup tool before answering status questions."
    config = ResolvedAgentConfig(
        prompt=prompt,
        model=AgentConfig(api_format="openai-responses", model_name="gpt-5.4-mini"),
        prompt_fingerprint="abc123def456",
        prompt_source="managed",
        model_source="managed",
    )
    history = [
        ModelRequest(parts=[UserPromptPart("Earlier question")]),
        ModelResponse(parts=[TextPart("Earlier answer")]),
    ]

    result = await run_applicant_turn(
        agent,
        question="What is my status?",
        deps=AgentDeps(database=database, application_id="APP-1001"),
        config=config,
        history=history,
        metadata={"session_id": "session-123", "force_online_evals": True},
        model=FunctionModel(respond),
    )

    assert result.output == "Configured reply"
    assert seen["instructions"] == prompt
    assert seen["messages"][:2] == history
    run_kwargs = seen["run_kwargs"]
    assert isinstance(run_kwargs, dict)
    assert run_kwargs["model"].model_name.startswith("function:respond")
    assert run_kwargs["message_history"] == history
    assert run_kwargs["metadata"] == {
        "application_id": "APP-1001",
        "resolved_model": "openai-responses:gpt-5.4-mini",
        "prompt_fingerprint": "abc123def456",
        "prompt_source": "managed",
        "model_source": "managed",
        "session_id": "session-123",
        "force_online_evals": True,
    }
    assert prompt not in repr(run_kwargs["metadata"])
