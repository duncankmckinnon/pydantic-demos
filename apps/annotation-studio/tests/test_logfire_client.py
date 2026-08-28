import json
from pathlib import Path

import pytest

import annotation_studio.logfire_client as logfire_client
from annotation_studio.logfire_client import (
    Cursor,
    decode_cursor,
    encode_cursor,
    parse_interaction,
    validate_agent_name,
    validate_trace_and_span,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_parse_interaction_extracts_input_and_output_for_new_turn() -> None:
    row = _load("real_span_trimmed.json")

    interaction = parse_interaction(row, trace_url="https://logfire-us.pydantic.dev/duncan/rx-assistant-demo?q=trace_id='x'")

    assert interaction.trace_id == "01a045b8d6d40acd6c98ee00f1a3fe93"
    assert interaction.span_id == "c7a2373c3fe61d3f"
    assert interaction.input_text == "What about major depressive disorder?"
    # final_result is present (even though scrubbed) so it wins over the assistant text —
    # scrubbed values are rendered as-is, no special handling.
    assert interaction.output_text == "[Scrubbed due to 'auth']"
    assert len(interaction.full_conversation) == 6
    assert interaction.raw_attributes is None


def test_parse_interaction_prefers_final_result_over_assistant_text() -> None:
    row = _load("final_result_present.json")

    interaction = parse_interaction(row, trace_url="https://example.test")

    assert interaction.input_text == "Is ibuprofen safe with warfarin?"
    assert interaction.output_text.startswith("No — ibuprofen")


def test_parse_interaction_falls_back_to_raw_attributes_when_messages_missing() -> None:
    row = _load("malformed_attributes.json")

    interaction = parse_interaction(row, trace_url="https://example.test")

    assert interaction.input_text == ""
    assert interaction.output_text == ""
    assert interaction.full_conversation == []
    assert interaction.raw_attributes == {"some_other_field": "value"}


def test_validate_agent_name_accepts_valid_names() -> None:
    validate_agent_name("rx_assistant_agent")  # does not raise


def test_validate_agent_name_rejects_sql_injection_attempt() -> None:
    with pytest.raises(ValueError):
        validate_agent_name("rx_assistant_agent' OR 1=1 --")


def test_validate_trace_and_span_accepts_real_ids() -> None:
    validate_trace_and_span("01a045b8d6d40acd6c98ee00f1a3fe93", "c7a2373c3fe61d3f")  # does not raise


@pytest.mark.parametrize(
    "trace_id,span_id",
    [("not-hex", "c7a2373c3fe61d3f"), ("01a045b8d6d40acd6c98ee00f1a3fe93", "too-short")],
)
def test_validate_trace_and_span_rejects_malformed_ids(trace_id: str, span_id: str) -> None:
    with pytest.raises(ValueError):
        validate_trace_and_span(trace_id, span_id)


def test_cursor_round_trip() -> None:
    cursor = Cursor(start_timestamp="2026-08-28T00:15:36.667186Z", span_id="c7a2373c3fe61d3f")

    assert decode_cursor(encode_cursor(cursor)) == cursor


def test_decode_cursor_rejects_malformed_input() -> None:
    with pytest.raises(ValueError):
        decode_cursor("not-valid-base64!!")


class FakeQueryClient:
    def __init__(self, rows, info=None, base_url="https://logfire-us.pydantic.dev"):
        self._rows = rows
        self._info = info or {"organization_name": "duncan", "project_name": "rx-assistant-demo"}
        self.base_url = base_url
        self.queries: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def info(self):
        return self._info

    async def query_json_rows(self, sql, min_timestamp=None, limit=None, **kwargs):
        self.queries.append({"sql": sql, "min_timestamp": min_timestamp, "limit": limit})
        return {"columns": [], "rows": self._rows[:limit] if limit is not None else self._rows}


def _row(trace_id: str, start_timestamp: str, span_id: str = "c7a2373c3fe61d3f") -> dict:
    return {
        "trace_id": trace_id,
        "span_id": span_id,
        "start_timestamp": start_timestamp,
        "duration": 1.0,
        "attributes": {
            "pydantic_ai.new_message_index": 0,
            "pydantic_ai.all_messages": [
                {"role": "user", "parts": [{"type": "text", "content": "hi"}]},
                {"role": "assistant", "parts": [{"type": "text", "content": "hello"}], "finish_reason": "stop"},
            ],
            "final_result": "hello",
        },
    }


def test_build_trace_link_matches_logfire_mcp_server_format() -> None:
    url = logfire_client.build_trace_link("https://logfire-us.pydantic.dev", "duncan", "rx-assistant-demo", "abc123")
    assert url == "https://logfire-us.pydantic.dev/duncan/rx-assistant-demo?q=trace_id='abc123'"


async def test_fetch_project_interactions_returns_parsed_rows_with_trace_urls(monkeypatch) -> None:
    fake_client = FakeQueryClient([_row("trace-1", "2026-08-28T00:00:00Z")])
    monkeypatch.setattr(logfire_client, "AsyncLogfireQueryClient", lambda read_token: fake_client)

    interactions, next_cursor = await logfire_client.fetch_project_interactions(
        "test-token", "rx_assistant_agent", cursor=None, limit=20
    )

    assert len(interactions) == 1
    assert interactions[0].trace_id == "trace-1"
    assert interactions[0].trace_url == "https://logfire-us.pydantic.dev/duncan/rx-assistant-demo?q=trace_id='trace-1'"
    assert next_cursor is None  # only 1 row came back for limit+1=21 — no next page
    assert "invoke_agent rx_assistant_agent" in fake_client.queries[0]["sql"]
    assert fake_client.queries[0]["limit"] == 21


async def test_fetch_project_interactions_sets_next_cursor_when_extra_row_exists(monkeypatch) -> None:
    rows = [_row(f"trace-{i}", f"2026-08-28T00:0{i}:00Z", span_id=f"000000000000000{i}") for i in range(3)]
    fake_client = FakeQueryClient(rows)
    monkeypatch.setattr(logfire_client, "AsyncLogfireQueryClient", lambda read_token: fake_client)

    interactions, next_cursor = await logfire_client.fetch_project_interactions(
        "test-token", "rx_assistant_agent", cursor=None, limit=2
    )

    assert len(interactions) == 2  # only page_size returned, not the peeked-ahead extra row
    assert next_cursor is not None
    decoded = logfire_client.decode_cursor(next_cursor)
    assert decoded.start_timestamp == interactions[-1].start_timestamp
    assert decoded.span_id == interactions[-1].span_id


async def test_fetch_project_interactions_applies_keyset_predicate_on_later_pages(monkeypatch) -> None:
    fake_client = FakeQueryClient([_row("trace-1", "2026-08-28T00:00:00Z")])
    monkeypatch.setattr(logfire_client, "AsyncLogfireQueryClient", lambda read_token: fake_client)
    cursor = logfire_client.encode_cursor(
        logfire_client.Cursor(start_timestamp="2026-08-28T00:05:00Z", span_id="c7a2373c3fe61d3f")
    )

    await logfire_client.fetch_project_interactions("test-token", "rx_assistant_agent", cursor=cursor, limit=20)

    sql = fake_client.queries[0]["sql"]
    assert "ORDER BY start_timestamp DESC, span_id DESC" in sql
    assert "c7a2373c3fe61d3f" in sql


async def test_fetch_project_interactions_rejects_invalid_agent_name() -> None:
    with pytest.raises(ValueError):
        await logfire_client.fetch_project_interactions(
            "test-token", "not valid; DROP TABLE records", cursor=None, limit=20
        )
