import json
from datetime import datetime, timedelta, timezone

from pydantic_evals.evaluators import EvaluatorContext
from pydantic_evals.online import OnlineEvaluator
from pydantic_evals.otel.span_tree import SpanNode, SpanTree

from chat.evals.dataset import chat_tool_use_judge
from chat.evals.online import CHAT_ONLINE_EVALUATION
from chat.evals.tool_use import ToolUseAppropriateness, _called_tool_names


def _span(span_id: int, name: str, attributes: dict) -> SpanNode:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return SpanNode(
        name=name,
        trace_id=1,
        span_id=span_id,
        parent_span_id=None,
        start_timestamp=start,
        end_timestamp=start + timedelta(seconds=1),
        attributes=attributes,
    )


def _output_messages_attr(*parts: dict) -> dict:
    return {"gen_ai.output.messages": json.dumps([{"role": "assistant", "parts": list(parts)}])}


def _ctx_with_spans(*spans: SpanNode) -> EvaluatorContext:
    tree = SpanTree()
    tree.add_spans(list(spans))
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


def test_extracts_client_executed_tool_from_execute_tool_span() -> None:
    ctx = _ctx_with_spans(_span(0, "execute_tool write_memory", {}))

    assert _called_tool_names(ctx) == ["write_memory"]


def test_extracts_native_tool_from_output_messages_builtin_part() -> None:
    ctx = _ctx_with_spans(
        _span(
            0,
            "chat claude-sonnet-5",
            _output_messages_attr({"type": "tool_call", "name": "web_search", "builtin": True}),
        )
    )

    assert _called_tool_names(ctx) == ["web_search"]


def test_ignores_non_builtin_tool_call_to_avoid_double_counting_local_tools() -> None:
    # A client-executed tool's own request also appears as a tool_call part on the model span,
    # but builtin=False there — it's already counted via its execute_tool span, so this path
    # must skip it or write_memory would be counted twice.
    ctx = _ctx_with_spans(
        _span(0, "execute_tool write_memory", {}),
        _span(
            1,
            "chat claude-sonnet-5",
            _output_messages_attr({"type": "tool_call", "name": "write_memory", "builtin": False}),
        ),
    )

    assert _called_tool_names(ctx) == ["write_memory"]


def test_no_tools_called_is_empty() -> None:
    ctx = _ctx_with_spans(_span(0, "chat claude-sonnet-5", {}))

    assert _called_tool_names(ctx) == []


def test_dataset_and_online_evaluation_share_the_same_tool_use_judge() -> None:
    online_judges = [
        entry.evaluator if isinstance(entry, OnlineEvaluator) else entry
        for entry in CHAT_ONLINE_EVALUATION.evaluators
    ]

    assert isinstance(chat_tool_use_judge, ToolUseAppropriateness)
    assert chat_tool_use_judge in online_judges
