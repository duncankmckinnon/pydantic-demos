from datetime import datetime, timedelta, timezone

from pydantic_evals.evaluators import EvaluationReason, EvaluatorContext
from pydantic_evals.online import OnlineEvaluator
from pydantic_evals.otel.span_tree import SpanNode, SpanTree

from rx_assistant.evals.db_judge import RxAssistantDbJudge, RxAssistantJudgment, _tool_call_sequence
from rx_assistant.evals.online import RX_ASSISTANT_ONLINE_EVALUATION, rx_assistant_db_judge


def _span(span_id: int, name: str) -> SpanNode:
    # Staggered by span_id (not a fixed timestamp) so _tool_call_sequence's sort-by-start-time
    # has a well-defined order to recover, matching how real spans never share a timestamp.
    start = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=span_id)
    return SpanNode(
        name=name,
        trace_id=1,
        span_id=span_id,
        parent_span_id=None,
        start_timestamp=start,
        end_timestamp=start + timedelta(seconds=1),
        attributes={},
    )


def _ctx_with_spans(*spans: SpanNode) -> EvaluatorContext:
    tree = SpanTree()
    tree.add_spans(list(spans))
    return EvaluatorContext(
        name="t",
        inputs="What treats ADHD?",
        output="Some reply",
        expected_output=None,
        metadata=None,
        duration=0.0,
        _span_tree=tree,
        attributes={},
        metrics={},
    )


def test_extracts_tool_call_sequence_in_call_order() -> None:
    ctx = _ctx_with_spans(_span(0, "execute_tool delegate_task"), _span(1, "execute_tool search_medications"))

    assert _tool_call_sequence(ctx) == ["delegate_task", "search_medications"]


def test_tool_call_sequence_preserves_repeats() -> None:
    ctx = _ctx_with_spans(
        _span(0, "execute_tool search_medications"),
        _span(1, "execute_tool search_medications"),
        _span(2, "execute_tool delegate_task"),
    )

    assert _tool_call_sequence(ctx) == ["search_medications", "search_medications", "delegate_task"]


def test_no_tools_called_is_empty() -> None:
    ctx = _ctx_with_spans(_span(0, "rx_assistant_agent claude-sonnet-4-6"))

    assert _tool_call_sequence(ctx) == []


class FakeResult:
    def __init__(self, output: RxAssistantJudgment) -> None:
        self.output = output


class FakeJudgeAgent:
    def __init__(self, output: RxAssistantJudgment) -> None:
        self._output = output
        self.calls: list[tuple[str, object]] = []

    async def run(self, prompt: str, deps=None):
        self.calls.append((prompt, deps))
        return FakeResult(self._output)


async def test_evaluate_returns_grounded_tool_use_and_trajectory_reasons(monkeypatch) -> None:
    judgment = RxAssistantJudgment(
        grounded=True,
        grounding_explanation="Medication found in database.",
        tool_use="appropriate",
        tool_use_explanation="Searched for a relevant condition.",
        trajectory="efficient",
        trajectory_explanation="Searched once and answered directly.",
    )
    fake_agent = FakeJudgeAgent(judgment)
    judge = RxAssistantDbJudge(agent=fake_agent)

    async def _fake_get_judge_deps():
        return "fake-deps"

    monkeypatch.setattr("rx_assistant.evals.db_judge._get_judge_deps", _fake_get_judge_deps)

    ctx = _ctx_with_spans(_span(0, "execute_tool search_medications"))
    result = await judge.evaluate(ctx)

    assert result == {
        "grounded": EvaluationReason(value=True, reason="Medication found in database."),
        "tool_use": EvaluationReason(value="appropriate", reason="Searched for a relevant condition."),
        "trajectory": EvaluationReason(value="efficient", reason="Searched once and answered directly."),
    }
    prompt, deps = fake_agent.calls[0]
    assert "search_medications" in prompt
    assert deps == "fake-deps"


def test_online_evaluation_wraps_the_db_judge() -> None:
    online_judges = [
        entry.evaluator if isinstance(entry, OnlineEvaluator) else entry
        for entry in RX_ASSISTANT_ONLINE_EVALUATION.evaluators
    ]

    assert rx_assistant_db_judge in online_judges
