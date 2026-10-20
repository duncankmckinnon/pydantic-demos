from datetime import datetime, timedelta, timezone

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_evals.evaluators import EvaluationReason, EvaluatorContext
from pydantic_evals.otel.span_tree import SpanNode, SpanTree

import applicant_support.evals.evaluators as evaluators_module
from applicant_support.evals.evaluators import (
    FrustrationHandling,
    GroundedCorrectness,
    Professionalism,
    ReplyPresent,
    ScoreJudgment,
    ToolUseAppropriateness,
    ToolUseJudgment,
    _called_tool_names,
    _get_tool_use_judge,
    _normalize_reply,
    _tool_return_evidence,
)
from applicant_support.evals.dataset import ApplicantSupportCaseMetadata
from applicant_support.evals.models import ApplicantSupportEvalOutput


def _span(span_id: int, name: str, attributes: dict | None = None) -> SpanNode:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return SpanNode(
        name=name,
        trace_id=1,
        span_id=span_id,
        parent_span_id=None,
        start_timestamp=start,
        end_timestamp=start + timedelta(seconds=1),
        attributes=attributes or {},
    )


def _ctx(
    *,
    question: str = "What is my application status?",
    output: str | ApplicantSupportEvalOutput = "You passed screening.",
    spans: tuple[SpanNode, ...] = (),
    metadata: ApplicantSupportCaseMetadata | dict | None = None,
) -> EvaluatorContext:
    tree = SpanTree()
    tree.add_spans(list(spans))
    return EvaluatorContext(
        name="case",
        inputs=question,
        output=output,
        expected_output=None,
        metadata=metadata,
        duration=0.0,
        _span_tree=tree,
        attributes={},
        metrics={},
    )


def _judge_agent(output_type, value) -> Agent:
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, value.model_dump())]
        )

    return Agent(FunctionModel(respond), output_type=output_type)


def test_reply_normalization_and_presence() -> None:
    evaluator = ReplyPresent()

    assert _normalize_reply("same") == _normalize_reply(
        ApplicantSupportEvalOutput(reply="same")
    )
    assert evaluator.evaluate(_ctx(output="useful")) is True
    assert evaluator.evaluate(_ctx(output="  \n")) is False


async def test_lookup_without_status_tool_is_missed_opportunity() -> None:
    evaluator = ToolUseAppropriateness(
        agent=_judge_agent(
            ToolUseJudgment,
            ToolUseJudgment(category="appropriate", explanation="not used"),
        )
    )

    result = await evaluator.evaluate(_ctx())

    assert result == EvaluationReason(
        value="missed_opportunity",
        reason="Application facts were requested without calling get_application_status.",
    )


async def test_unnecessary_tool_judgment_keeps_reason() -> None:
    evaluator = ToolUseAppropriateness(
        agent=_judge_agent(
            ToolUseJudgment,
            ToolUseJudgment(
                category="unnecessary", explanation="A greeting needed no role search."
            ),
        )
    )
    ctx = _ctx(
        question="Hello there",
        spans=(_span(1, "execute_tool search_roles"),),
    )

    result = await evaluator.evaluate(ctx)

    assert _called_tool_names(ctx) == ["search_roles"]
    assert result.value == "unnecessary"
    assert result.reason == "A greeting needed no role search."


async def test_required_tool_metadata_detects_a_missed_non_status_tool() -> None:
    evaluator = ToolUseAppropriateness(
        agent=_judge_agent(
            ToolUseJudgment,
            ToolUseJudgment(category="appropriate", explanation="not used"),
        )
    )
    ctx = _ctx(
        question="Can you move my recruiter call?",
        metadata=ApplicantSupportCaseMetadata(
            application_id="APP-1001",
            category="rescheduling",
            complexity="complex",
            provenance="seed",
            tags=["mutation", "scheduling"],
            required_tool="reschedule_screening_call",
        ),
    )

    result = await evaluator.evaluate(ctx)

    assert result == EvaluationReason(
        value="missed_opportunity",
        reason=(
            "The case required reschedule_screening_call, but that tool was not called."
        ),
    )


async def test_required_tool_metadata_replaces_online_keyword_fallback() -> None:
    evaluator = ToolUseAppropriateness(
        agent=_judge_agent(
            ToolUseJudgment,
            ToolUseJudgment(
                category="appropriate", explanation="The required tool was used."
            ),
        )
    )
    ctx = _ctx(
        question="Can you move my screening call?",
        spans=(_span(1, "execute_tool reschedule_screening_call"),),
        metadata=ApplicantSupportCaseMetadata(
            application_id="APP-1001",
            category="rescheduling",
            complexity="complex",
            provenance="seed",
            tags=["mutation", "scheduling"],
            required_tool="reschedule_screening_call",
        ),
    )

    result = await evaluator.evaluate(ctx)

    assert result == EvaluationReason(
        value="appropriate", reason="The required tool was used."
    )


async def test_default_evaluator_constructs_its_judge_lazily(monkeypatch) -> None:
    calls: list[tuple[str, type, str]] = []
    judge = _judge_agent(
        ToolUseJudgment,
        ToolUseJudgment(category="appropriate", explanation="No tool was needed."),
    )

    def fake_judge_agent(name, output_type, instructions):
        calls.append((name, output_type, instructions))
        return judge

    _get_tool_use_judge.cache_clear()
    monkeypatch.setattr(
        "applicant_support.evals.evaluators._judge_agent", fake_judge_agent
    )
    evaluator = ToolUseAppropriateness()

    assert evaluator.agent is None
    result = await evaluator.evaluate(_ctx(question="Hello there"))

    assert result.value == "appropriate"
    assert [name for name, _, _ in calls] == ["applicant_support_tool_use_judge"]


async def test_grounded_correctness_judge_receives_tool_return_evidence() -> None:
    evidence = '{"status":"screening_passed","applicant":"Maya Chen"}'

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        serialized = repr(messages)
        assert "screening_passed" in serialized
        assert "Maya Chen" in serialized
        value = ScoreJudgment(score=1.0, explanation="The reply matches the lookup.")
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, value.model_dump())]
        )

    evaluator = GroundedCorrectness(
        agent=Agent(FunctionModel(respond), output_type=ScoreJudgment)
    )
    ctx = _ctx(
        spans=(
            _span(
                1,
                "execute_tool get_application_status",
                {"gen_ai.tool.call.result": evidence},
            ),
        )
    )

    result = await evaluator.evaluate(ctx)

    assert evidence in _tool_return_evidence(ctx)[0]
    assert result.value == 1.0
    assert result.reason == "The reply matches the lookup."


async def test_professionalism_and_frustration_preserve_structured_reasons() -> None:
    professional = Professionalism(
        agent=_judge_agent(
            ScoreJudgment,
            ScoreJudgment(score=0.9, explanation="Clear and respectful."),
        )
    )
    frustration = FrustrationHandling(
        agent=_judge_agent(
            ScoreJudgment,
            ScoreJudgment(score=0.8, explanation="Acknowledges the delay."),
        )
    )
    ctx = _ctx(question="I am frustrated that nobody has replied.")

    professional_result = await professional.evaluate(ctx)
    frustration_result = await frustration.evaluate(ctx)

    assert professional_result == EvaluationReason(
        value=0.9, reason="Clear and respectful."
    )
    assert frustration_result == EvaluationReason(
        value=0.8, reason="Acknowledges the delay."
    )


async def test_quality_bundle_emits_components_and_normalized_overall_score() -> None:
    quality_type = getattr(evaluators_module, "ApplicantSupportQuality", None)
    assert quality_type is not None

    evaluator = quality_type(
        tool_use=ToolUseAppropriateness(
            agent=_judge_agent(
                ToolUseJudgment,
                ToolUseJudgment(
                    category="appropriate", explanation="The tool choice was correct."
                ),
            )
        ),
        grounded_correctness=GroundedCorrectness(
            agent=_judge_agent(
                ScoreJudgment,
                ScoreJudgment(score=0.8, explanation="Mostly grounded."),
            )
        ),
        professionalism=Professionalism(
            agent=_judge_agent(
                ScoreJudgment,
                ScoreJudgment(score=0.6, explanation="Professional enough."),
            )
        ),
        frustration_handling=FrustrationHandling(
            agent=_judge_agent(
                ScoreJudgment,
                ScoreJudgment(score=1.0, explanation="Handled well."),
            )
        ),
    )

    results = await evaluator.evaluate(_ctx(question="Hello", output="A useful reply"))

    assert results["ReplyPresent"].value == 1.0
    assert results["ToolUseAppropriateness"].value == "appropriate"
    assert results["ToolUseScore"].value == 1.0
    assert results["GroundedCorrectness"].value == 0.8
    assert results["Professionalism"].value == 0.6
    assert results["FrustrationHandling"].value == 1.0
    assert results["OverallQuality"].value == pytest.approx(0.85)
