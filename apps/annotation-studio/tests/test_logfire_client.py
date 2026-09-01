import json
from datetime import datetime, timezone
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
    assert interaction.raw_row is None


def test_parse_interaction_treats_missing_new_message_index_as_zero() -> None:
    # A first-turn conversation has no prior messages to index past, so its correct
    # new_message_index is 0 — and the instrumentation omits zero-valued attributes
    # entirely rather than emitting an explicit 0. Absence here must not be treated as
    # malformed data (which would otherwise fall back to raw_row for every first-turn
    # interaction — the common case, not an edge case).
    row = {
        "trace_id": "01a045b8d6d40acd6c98ee00f1a3fe93",
        "span_id": "c7a2373c3fe61d3f",
        "start_timestamp": "2026-08-28T00:00:00Z",
        "duration": 1.0,
        "attributes": {
            "pydantic_ai.all_messages": [
                {"role": "user", "parts": [{"type": "text", "content": "hi"}]},
                {"role": "assistant", "parts": [{"type": "text", "content": "hello"}], "finish_reason": "stop"},
            ],
            "final_result": "hello",
        },
    }

    interaction = parse_interaction(row, trace_url="https://example.test")

    assert interaction.raw_row is None
    assert interaction.input_text == "hi"
    assert interaction.output_text == "hello"


def test_parse_interaction_prefers_final_result_over_assistant_text() -> None:
    row = _load("final_result_present.json")

    interaction = parse_interaction(row, trace_url="https://example.test")

    assert interaction.input_text == "Is ibuprofen safe with warfarin?"
    assert interaction.output_text.startswith("No — ibuprofen")


def test_parse_interaction_falls_back_to_raw_row_when_messages_missing() -> None:
    row = _load("malformed_attributes.json")

    interaction = parse_interaction(row, trace_url="https://example.test")

    assert interaction.input_text == ""
    assert interaction.output_text == ""
    assert interaction.full_conversation == []
    assert interaction.raw_row == {
        "duration": 0.5,
        "attributes": {"some_other_field": "value"},
    }


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
    assert url == "https://logfire-us.pydantic.dev/duncan/rx-assistant-demo?q=trace_id='abc123'&last=%2214d%22"


def test_build_trace_link_lookback_window_matches_fetch_project_interactions_bound() -> None:
    # The link's `last=` window must cover everything fetch_project_interactions can ever
    # return (its own min_timestamp bound below) — otherwise "Open trace in Logfire" could
    # open to an empty page for a trace this app itself is showing in the interaction list.
    url = logfire_client.build_trace_link("https://logfire-us.pydantic.dev", "duncan", "rx-assistant-demo", "abc123")
    assert f"%22{logfire_client.LOOKBACK_DAYS}d%22" in url


async def test_fetch_project_interactions_returns_parsed_rows_with_trace_urls(monkeypatch) -> None:
    fake_client = FakeQueryClient([_row("trace-1", "2026-08-28T00:00:00Z")])
    monkeypatch.setattr(logfire_client, "AsyncLogfireQueryClient", lambda read_token: fake_client)

    interactions, next_cursor = await logfire_client.fetch_project_interactions(
        "test-token", "rx_assistant_agent", cursor=None, limit=20
    )

    assert len(interactions) == 1
    assert interactions[0].trace_id == "trace-1"
    assert interactions[0].trace_url == "https://logfire-us.pydantic.dev/duncan/rx-assistant-demo?q=trace_id='trace-1'&last=%2214d%22"
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


def test_validate_query_accepts_a_valid_select() -> None:
    query = "SELECT trace_id, span_id, start_timestamp FROM records WHERE span_name = 'x'"
    assert logfire_client.validate_query(query) == query


def test_validate_query_strips_single_trailing_semicolon() -> None:
    assert logfire_client.validate_query("SELECT 1;") == "SELECT 1"


def test_validate_query_accepts_a_cte_starting_with_with() -> None:
    query = (
        "WITH interactions AS (SELECT trace_id FROM records) "
        "SELECT trace_id FROM interactions ORDER BY trace_id"
    )
    assert logfire_client.validate_query(query) == query


@pytest.mark.parametrize(
    "query",
    [
        "",
        "   ",
        "UPDATE records SET x = 1",
        "SELECT 1; DROP TABLE records",
        "SELECT 1 -- comment; SELECT 2",
        "DELETE FROM records",
        "x" * 5001,
    ],
)
def test_validate_query_rejects_unsafe_or_invalid_queries(query: str) -> None:
    with pytest.raises(ValueError):
        logfire_client.validate_query(query)


def test_sample_included_is_deterministic_for_the_same_item() -> None:
    results = {
        logfire_client.sample_included(1, "trace-1", "span-1", 50) for _ in range(20)
    }
    assert len(results) == 1


def test_sample_included_100_percent_always_included() -> None:
    for i in range(50):
        assert logfire_client.sample_included(1, f"trace-{i}", "span-1", 100) is True


def test_sample_included_roughly_matches_percentage_across_many_items() -> None:
    included = sum(
        logfire_client.sample_included(1, f"trace-{i}", "span-1", 30) for i in range(2000)
    )
    assert 500 < included < 900


class FakeErroringQueryClient(FakeQueryClient):
    async def query_json_rows(self, sql, min_timestamp=None, limit=None, **kwargs):
        raise RuntimeError("simulated Logfire query error")


async def test_validate_query_columns_passes_for_a_row_with_required_columns(monkeypatch) -> None:
    fake_client = FakeQueryClient([_row("trace-1", "2026-08-28T00:00:00Z")])
    monkeypatch.setattr(logfire_client, "AsyncLogfireQueryClient", lambda read_token: fake_client)

    await logfire_client.validate_query_columns("test-token", "SELECT * FROM records")

    assert "LIMIT 1" in fake_client.queries[0]["sql"]


async def test_validate_query_columns_raises_when_required_column_missing(monkeypatch) -> None:
    fake_client = FakeQueryClient([{"trace_id": "t1", "span_id": "s1"}])
    monkeypatch.setattr(logfire_client, "AsyncLogfireQueryClient", lambda read_token: fake_client)

    with pytest.raises(ValueError, match="start_timestamp"):
        await logfire_client.validate_query_columns("test-token", "SELECT trace_id, span_id FROM records")


async def test_validate_query_columns_passes_when_query_currently_matches_nothing(monkeypatch) -> None:
    fake_client = FakeQueryClient([])
    monkeypatch.setattr(logfire_client, "AsyncLogfireQueryClient", lambda read_token: fake_client)

    await logfire_client.validate_query_columns("test-token", "SELECT * FROM records WHERE 1=0")


async def test_validate_query_columns_wraps_logfire_errors(monkeypatch) -> None:
    fake_client = FakeErroringQueryClient([])
    monkeypatch.setattr(logfire_client, "AsyncLogfireQueryClient", lambda read_token: fake_client)

    with pytest.raises(ValueError, match="simulated Logfire query error"):
        await logfire_client.validate_query_columns("test-token", "SELECT * FROM nonsense")


async def test_fetch_queue_matches_returns_rows_with_required_columns(monkeypatch) -> None:
    fake_client = FakeQueryClient([_row("trace-1", "2026-08-28T00:00:00Z")])
    monkeypatch.setattr(logfire_client, "AsyncLogfireQueryClient", lambda read_token: fake_client)
    window_start = datetime(2026, 8, 27, tzinfo=timezone.utc)
    window_end = datetime(2026, 8, 28, tzinfo=timezone.utc)

    rows = await logfire_client.fetch_queue_matches(
        "test-token", "SELECT * FROM records", window_start, window_end, limit=100
    )

    assert len(rows) == 1
    assert fake_client.queries[0]["min_timestamp"] == window_start


async def test_fetch_queue_matches_skips_rows_missing_required_columns(monkeypatch) -> None:
    fake_client = FakeQueryClient([{"trace_id": "t1"}])
    monkeypatch.setattr(logfire_client, "AsyncLogfireQueryClient", lambda read_token: fake_client)

    rows = await logfire_client.fetch_queue_matches(
        "test-token", "SELECT * FROM records", datetime.now(timezone.utc), datetime.now(timezone.utc), limit=100
    )

    assert rows == []


async def test_fetch_queue_item_content_keys_by_trace_and_span(monkeypatch) -> None:
    trace_id = "01a045b8d6d40acd6c98ee00f1a3fe93"
    fake_client = FakeQueryClient([_row(trace_id, "2026-08-28T00:00:00Z", span_id="c7a2373c3fe61d3f")])
    monkeypatch.setattr(logfire_client, "AsyncLogfireQueryClient", lambda read_token: fake_client)

    content = await logfire_client.fetch_queue_item_content(
        "test-token", [(trace_id, "c7a2373c3fe61d3f")]
    )

    assert (trace_id, "c7a2373c3fe61d3f") in content
    assert content[(trace_id, "c7a2373c3fe61d3f")].input_text == "hi"


async def test_fetch_queue_item_content_omits_pairs_not_returned(monkeypatch) -> None:
    trace_id = "01a045b8d6d40acd6c98ee00f1a3fe93"
    fake_client = FakeQueryClient([])
    monkeypatch.setattr(logfire_client, "AsyncLogfireQueryClient", lambda read_token: fake_client)

    content = await logfire_client.fetch_queue_item_content("test-token", [(trace_id, "c7a2373c3fe61d3f")])

    assert content == {}


async def test_fetch_queue_item_content_returns_empty_dict_for_no_items() -> None:
    assert await logfire_client.fetch_queue_item_content("test-token", []) == {}


async def test_fetch_queue_item_content_rejects_malformed_ids() -> None:
    with pytest.raises(ValueError):
        await logfire_client.fetch_queue_item_content("test-token", [("not-hex", "c7a2373c3fe61d3f")])


async def test_fetch_logfire_project_info_returns_base_url_org_and_project(monkeypatch) -> None:
    fake_client = FakeQueryClient([])
    monkeypatch.setattr(logfire_client, "AsyncLogfireQueryClient", lambda read_token: fake_client)

    info = await logfire_client.fetch_logfire_project_info("test-token")

    assert info == {
        "base_url": "https://logfire-us.pydantic.dev",
        "organization_name": "duncan",
        "project_name": "rx-assistant-demo",
    }


def test_build_explore_link_includes_project_path_and_query() -> None:
    url = logfire_client.build_explore_link(
        "https://logfire-us.pydantic.dev", "duncan", "rx-assistant-demo", "SELECT 1"
    )
    assert url.startswith("https://logfire-us.pydantic.dev/duncan/rx-assistant-demo/explore?q=")
    assert "SELECT" in url
