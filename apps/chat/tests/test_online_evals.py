from pydantic_evals.online import OnlineEvaluator

from chat.evals.dataset import chat_quality_judge
from chat.evals.online import CHAT_ONLINE_EVALUATION, ReplyNotEmpty


def test_online_evaluation_wraps_expected_evaluators() -> None:
    evaluators = [
        entry.evaluator if isinstance(entry, OnlineEvaluator) else entry
        for entry in CHAT_ONLINE_EVALUATION.evaluators
    ]

    assert any(isinstance(ev, ReplyNotEmpty) for ev in evaluators)
    assert chat_quality_judge in evaluators


def test_reply_not_empty_evaluator() -> None:
    evaluator = ReplyNotEmpty()

    assert evaluator.evaluate(_ctx(output="hello")) is True
    assert evaluator.evaluate(_ctx(output="")) is False
    assert evaluator.evaluate(_ctx(output="   ")) is False


def _ctx(output: str):
    from pydantic_evals.evaluators import EvaluatorContext
    from pydantic_evals.otel._errors import SpanTreeRecordingError

    return EvaluatorContext(
        name="t",
        inputs="hi",
        output=output,
        expected_output=None,
        metadata=None,
        duration=0.0,
        _span_tree=SpanTreeRecordingError("no tracer configured in this unit test"),
        attributes={},
        metrics={},
    )
