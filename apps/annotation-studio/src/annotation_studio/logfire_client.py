import base64
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from logfire.experimental.query_client import AsyncLogfireQueryClient

AGENT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")
TRACE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
SPAN_ID_PATTERN = re.compile(r"^[0-9a-f]{16}$")

QUERY_MAX_LENGTH = 5000
REQUIRED_QUEUE_COLUMNS = ("trace_id", "span_id", "start_timestamp")

_SELECT_PREFIX = re.compile(r"^\s*SELECT\b", re.IGNORECASE)
_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|MERGE|EXEC|CALL)\b",
    re.IGNORECASE,
)


def validate_query(query: str) -> str:
    """Defense-in-depth validation of a queue's user-authored SQL — Logfire's query endpoint
    is the real read-only boundary; this just fails fast with a clear message before an
    obviously bad or unsafe-looking query is ever sent. Returns the query with a single
    trailing ';' stripped (a harmless habit, not worth rejecting)."""
    stripped = query.strip()
    if stripped.endswith(";"):
        stripped = stripped[:-1].rstrip()
    if not stripped:
        raise ValueError("Query cannot be empty")
    if len(stripped) > QUERY_MAX_LENGTH:
        raise ValueError(f"Query is too long (max {QUERY_MAX_LENGTH} characters)")
    if not _SELECT_PREFIX.match(stripped):
        raise ValueError("Query must start with SELECT")
    if ";" in stripped:
        raise ValueError("Query must be a single statement (no ';')")
    if _FORBIDDEN_KEYWORDS.search(stripped):
        raise ValueError("Query must be read-only (no INSERT/UPDATE/DELETE/DDL keywords)")
    return stripped


def sample_included(queue_id: int, trace_id: str, span_id: str, sampling_percentage: int) -> bool:
    """Deterministic per-item sampling decision: the same (queue, trace, span) always yields
    the same answer across repeated refreshes, so a queue can grow over time without an
    already-shown item ever dropping out."""
    key = f"{queue_id}:{trace_id}:{span_id}".encode("utf-8")
    digest = hashlib.md5(key).hexdigest()
    bucket = int(digest[:8], 16) % 100
    return bucket < sampling_percentage


async def validate_query_columns(read_token: str, query: str) -> None:
    """Runs `query` once with LIMIT 1 to confirm it executes and, if it returns a row, that the
    row has the required columns. A query that currently matches nothing still passes — Logfire
    doesn't hand back column metadata for an empty result here — so a query missing a required
    column but matching nothing yet only surfaces later, as a clear per-item error at refresh
    time (fetch_queue_matches below already tolerates that by skipping such rows)."""
    wrapped = f"SELECT * FROM ({query}) AS queue_query LIMIT 1"
    async with AsyncLogfireQueryClient(read_token) as client:
        try:
            result = await client.query_json_rows(wrapped, limit=1)
        except Exception as exc:
            raise ValueError(f"Query failed against Logfire: {exc}") from exc
    rows = result.get("rows", [])
    if rows:
        missing = [c for c in REQUIRED_QUEUE_COLUMNS if c not in rows[0]]
        if missing:
            raise ValueError(f"Query result is missing required column(s): {', '.join(missing)}")


async def fetch_queue_matches(
    read_token: str, query: str, min_timestamp: datetime, max_timestamp: datetime, limit: int
) -> list[dict]:
    """Runs a queue's arbitrary SELECT and returns rows with at least trace_id/span_id/
    start_timestamp — rows missing any of those (a query edited after being validated, or a
    projection that dropped a column) are skipped rather than crashing the whole refresh."""
    async with AsyncLogfireQueryClient(read_token) as client:
        result = await client.query_json_rows(query, min_timestamp=min_timestamp, max_timestamp=max_timestamp, limit=limit)
    return [row for row in result["rows"] if all(key in row for key in REQUIRED_QUEUE_COLUMNS)]


def build_explore_link(base_url: str, organization_name: str, project_name: str, query: str) -> str:
    # Best-effort: Logfire's docs don't confirm the Explore page reads a `q` URL param the way
    # the live view does (see build_trace_link) — if it doesn't, this just opens Explore itself,
    # which is still useful. The frontend's "Copy query" button is the reliable path.
    return f"{base_url}/{organization_name}/{project_name}/explore?q={quote(query)}"


async def fetch_queue_item_content(read_token: str, items: list[tuple[str, str]]) -> dict[tuple[str, str], "Interaction"]:
    """Batch-fetches full row content for a page of queue items, keyed by (trace_id, span_id).
    A pair whose trace has aged out of the 14-day query window (or was otherwise not found) is
    simply absent from the returned dict; callers render a placeholder for it rather than
    failing the whole page."""
    if not items:
        return {}
    for trace_id, span_id in items:
        validate_trace_and_span(trace_id, span_id)
    predicates = " OR ".join(f"(trace_id = '{t}' AND span_id = '{s}')" for t, s in items)
    sql = f"SELECT trace_id, span_id, start_timestamp, duration, attributes FROM records WHERE {predicates}"
    min_timestamp = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    content: dict[tuple[str, str], Interaction] = {}
    async with AsyncLogfireQueryClient(read_token) as client:
        info = await client.info()
        result = await client.query_json_rows(sql, min_timestamp=min_timestamp, limit=len(items))
        for row in result["rows"]:
            trace_url = build_trace_link(client.base_url, info["organization_name"], info["project_name"], row["trace_id"])
            content[(row["trace_id"], row["span_id"])] = parse_interaction(row, trace_url)
    return content

# How far back an interaction can be and still show up in fetch_project_interactions
# (its own min_timestamp bound, below) — also used as the trace link's lookback window so
# "Open trace in Logfire" is guaranteed to actually find the trace: the Logfire UI's own
# default lookback is much shorter than this, and without an explicit `last=` window an
# older trace's page would just render empty despite the trace_id filter being correct.
LOOKBACK_DAYS = 14


def validate_agent_name(name: str) -> None:
    """Raise ValueError if `name` isn't safe to interpolate into the SQL span-name filter
    below — it comes from a project's stored, UI-editable top_level_agent_name, so this is
    the only thing standing between that field and a SQL-injection into Logfire's query
    engine."""
    if not AGENT_NAME_PATTERN.match(name):
        raise ValueError(f"Invalid top_level_agent_name: {name!r}")


def validate_trace_and_span(trace_id: str, span_id: str) -> None:
    """Raise ValueError if either id isn't a well-formed W3C trace/span id — guards both the
    keyset-pagination cursor below (span_id gets interpolated into a SQL predicate) and the
    write-back traceparent construction in logfire_writer.py (Task 4)."""
    if not TRACE_ID_PATTERN.match(trace_id):
        raise ValueError(f"Invalid trace_id: {trace_id!r}")
    if not SPAN_ID_PATTERN.match(span_id):
        raise ValueError(f"Invalid span_id: {span_id!r}")


@dataclass(frozen=True)
class Cursor:
    start_timestamp: str
    span_id: str


def encode_cursor(cursor: Cursor) -> str:
    payload = json.dumps(asdict(cursor)).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def decode_cursor(value: str) -> Cursor:
    try:
        payload = json.loads(base64.urlsafe_b64decode(value.encode("ascii")))
        start_timestamp = payload["start_timestamp"]
        span_id = payload["span_id"]
        datetime.fromisoformat(start_timestamp)  # raises ValueError if malformed
        if not SPAN_ID_PATTERN.match(span_id):
            raise ValueError(f"Invalid span_id in cursor: {span_id!r}")
    except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
        raise ValueError(f"Invalid cursor: {value!r}") from exc
    return Cursor(start_timestamp=start_timestamp, span_id=span_id)


@dataclass
class Interaction:
    trace_id: str
    span_id: str
    start_timestamp: str
    input_text: str
    output_text: str
    full_conversation: list[dict]
    trace_url: str
    raw_row: dict | None = None


def _text_content(message: dict) -> str | None:
    for part in message.get("parts", []):
        if part.get("type") == "text":
            return part.get("content")
    return None


def parse_interaction(row: dict, trace_url: str) -> Interaction:
    """Extract this turn's input/output from one `invoke_agent` span row.

    `new_message_index` marks where THIS turn's new messages begin in `all_messages` — the
    turn's input is the first new user text message (skipping tool_call_response messages,
    which also carry role='user'), and its output is `final_result` if Logfire captured one
    (even a scrubbed placeholder — rendered as-is), otherwise the last new assistant text
    message. If `all_messages`/`new_message_index` are missing or malformed, falls back to
    the raw attributes so nothing is silently hidden from the reviewer.
    """
    attributes = row.get("attributes") or {}
    all_messages = attributes.get("pydantic_ai.all_messages")
    new_message_index = attributes.get("pydantic_ai.new_message_index")

    if not isinstance(all_messages, list) or not isinstance(new_message_index, int):
        return Interaction(
            trace_id=row["trace_id"],
            span_id=row["span_id"],
            start_timestamp=row["start_timestamp"],
            input_text="",
            output_text="",
            full_conversation=[],
            trace_url=trace_url,
            raw_row={k: v for k, v in row.items() if k not in ("trace_id", "span_id", "start_timestamp")},
        )

    new_messages = all_messages[new_message_index:]

    input_text = ""
    for message in new_messages:
        if message.get("role") == "user":
            text = _text_content(message)
            if text is not None:
                input_text = text
                break

    final_result = attributes.get("final_result")
    if isinstance(final_result, str) and final_result:
        output_text = final_result
    else:
        output_text = ""
        for message in reversed(new_messages):
            if message.get("role") == "assistant":
                text = _text_content(message)
                if text is not None:
                    output_text = text
                    break

    return Interaction(
        trace_id=row["trace_id"],
        span_id=row["span_id"],
        start_timestamp=row["start_timestamp"],
        input_text=input_text,
        output_text=output_text,
        full_conversation=all_messages,
        trace_url=trace_url,
    )


def build_trace_link(base_url: str, organization_name: str, project_name: str, trace_id: str) -> str:
    # `last` takes a JSON-quoted duration string (e.g. `"14d"`, matching what the Logfire UI
    # itself writes into the URL when you pick a time range there) — quote() percent-encodes
    # the literal double quotes the same way.
    last = quote(f'"{LOOKBACK_DAYS}d"')
    return f"{base_url}/{organization_name}/{project_name}?q=trace_id='{trace_id}'&last={last}"


async def fetch_project_interactions(
    read_token: str,
    top_level_agent_name: str,
    cursor: str | None,
    limit: int,
) -> tuple[list[Interaction], str | None]:
    """Fetch one page of interactions (most-recent-first) for `top_level_agent_name`, each
    with its Logfire trace URL already attached, using exclusive keyset pagination over
    `(start_timestamp, span_id)` so a page boundary can neither skip nor duplicate a row when
    several spans share a timestamp (a real risk with fast automated traffic). `cursor` is the
    opaque encoding of the last row already shown (None for the first page). Requests one row
    beyond `limit` to detect whether another page exists; `next_cursor` is None once the extra
    row isn't there.
    """
    validate_agent_name(top_level_agent_name)

    decoded = decode_cursor(cursor) if cursor else None
    min_timestamp = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    sql = (
        "SELECT trace_id, span_id, start_timestamp, duration, attributes "
        "FROM records "
        f"WHERE span_name = 'invoke_agent {top_level_agent_name}' "
    )
    if decoded is not None:
        # decode_cursor already validated these as an ISO timestamp and a 16-hex span id —
        # query_json_rows has no way to bind this second predicate as a parameter, so an
        # unvalidated cursor would otherwise be a SQL-injection surface here.
        sql += (
            f"AND (start_timestamp < timestamp '{decoded.start_timestamp}' "
            f"OR (start_timestamp = timestamp '{decoded.start_timestamp}' "
            f"AND span_id < '{decoded.span_id}')) "
        )
    sql += "ORDER BY start_timestamp DESC, span_id DESC"

    async with AsyncLogfireQueryClient(read_token) as client:
        info = await client.info()
        result = await client.query_json_rows(sql, min_timestamp=min_timestamp, limit=limit + 1)
        rows = result["rows"]
        interactions = [
            parse_interaction(
                row,
                build_trace_link(client.base_url, info["organization_name"], info["project_name"], row["trace_id"]),
            )
            for row in rows[:limit]
        ]

    next_cursor = None
    if len(rows) > limit:
        last = interactions[-1]
        next_cursor = encode_cursor(Cursor(start_timestamp=last.start_timestamp, span_id=last.span_id))
    return interactions, next_cursor
