from datetime import datetime, timedelta, timezone

from pydantic_evals.evaluators import EvaluatorContext
from pydantic_evals.otel._errors import SpanTreeRecordingError
from pydantic_evals.otel.span_tree import SpanNode, SpanTree

from chat.evals.dataset import chat_efficiency_judge
from chat.evals.efficiency import ResponseEfficiency, _sum_token_usage
from chat.evals.online import CHAT_ONLINE_EVALUATION
from pydantic_evals.online import OnlineEvaluator


def _span(span_id: int, attributes: dict) -> SpanNode:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return SpanNode(
        name="span",
        trace_id=1,
        span_id=span_id,
        parent_span_id=None,
        start_timestamp=start,
        end_timestamp=start + timedelta(seconds=1),
        attributes=attributes,
    )


def _ctx_with_spans(*attributes: dict) -> EvaluatorContext:
    tree = SpanTree()
    tree.add_spans([_span(i, attrs) for i, attrs in enumerate(attributes)])
    return EvaluatorContext(
        name="t",
        inputs="hi",
        output="hello",
        expected_output=None,
        metadata=None,
        duration=0.0,
        _span_tree=tree,
        attributes={},
        metrics={},
    )


def test_sum_token_usage_sums_across_spans() -> None:
    ctx = _ctx_with_spans(
        {"gen_ai.usage.input_tokens": 10, "gen_ai.usage.output_tokens": 5},
        {"gen_ai.usage.input_tokens": 3, "gen_ai.usage.output_tokens": 2},
        {"unrelated": "value"},
    )

    assert _sum_token_usage(ctx) == (13, 7)


def test_sum_token_usage_ignores_aggregated_attributes() -> None:
    # gen_ai.aggregated_usage.* is the agent-run span's own total, kept separate precisely so
    # summing gen_ai.usage.* across a trace doesn't double-count against it.
    ctx = _ctx_with_spans(
        {"gen_ai.usage.input_tokens": 10, "gen_ai.usage.output_tokens": 5},
        {"gen_ai.aggregated_usage.input_tokens": 10, "gen_ai.aggregated_usage.output_tokens": 5},
    )

    assert _sum_token_usage(ctx) == (10, 5)


def test_sum_token_usage_with_no_matching_spans_is_zero() -> None:
    ctx = _ctx_with_spans({"unrelated": "value"})

    assert _sum_token_usage(ctx) == (0, 0)


def test_dataset_and_online_evaluation_share_the_same_efficiency_judge() -> None:
    online_judges = [
        entry.evaluator if isinstance(entry, OnlineEvaluator) else entry
        for entry in CHAT_ONLINE_EVALUATION.evaluators
    ]

    assert isinstance(chat_efficiency_judge, ResponseEfficiency)
    assert chat_efficiency_judge in online_judges
