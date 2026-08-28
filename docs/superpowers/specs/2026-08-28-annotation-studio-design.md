# annotation-studio Demo — Design

Date: 2026-08-28
Status: Approved for implementation planning

## Motivation

Reviewers grading `rx-assistant`'s agent responses currently have no dedicated tool: they'd
need to read raw traces in the Logfire UI and track verdicts somewhere else by hand. This
adds a new demo, `annotation-studio`, whose whole purpose is that grading workflow: browse
recent `rx-assistant` agent interactions pulled live from Logfire, read each one's full
input/output, and record a label plus a written justification against a per-project grading
criteria block.

This follows the same `apps/<name>` + `demo_core` pattern as `chat` and `rx-assistant`, but
it is the first demo that is a *consumer* of another demo's telemetry rather than a producer
of its own agent traces — `annotation-studio` runs no Pydantic AI agent itself. It's also the
first demo with a JS build step (React + Vite), a deliberate deviation from every other
demo's server-rendered-Jinja/no-build-step approach, made because the review interaction
(inline label picking, expand/collapse, editable justifications) is meaningfully easier to
build well as a small SPA than as server-rendered HTML + vanilla JS.

## Scope (v1)

- One fixed source project: `rx-assistant`'s Logfire project (`rx-assistant-demo`). The
  other two Logfire projects in this org are out of scope until/unless their trace shape is
  confirmed to match — see "Interaction identification" below.
- Read-only access to that project's traces. **No write-back onto the source trace** — see
  "Out of Scope."
- No auth (repo-wide convention — everything here is local-only).

## Data Model (SQLite)

`annotation-studio` is the only demo so far needing simple structured local state without a
production-shaped database, so plain stdlib `sqlite3` is used directly — no new ORM
dependency, one file at `ANNOTATION_STUDIO_DATABASE_PATH` (default
`data/annotation_studio.sqlite3`, gitignored, backed by a named Docker volume in Compose so
it survives container restarts).

```sql
CREATE TABLE projects (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,                    -- display name, e.g. 'rx-assistant'
    top_level_agent_name TEXT NOT NULL,    -- e.g. 'rx_assistant_agent' — see below
    criteria_text TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE labels (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    name TEXT NOT NULL,                    -- e.g. 'Pass', 'Hallucination'
    sort_order INTEGER NOT NULL DEFAULT 0,
    UNIQUE(project_id, name)
);

CREATE TABLE annotations (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    trace_id TEXT NOT NULL,
    span_id TEXT NOT NULL,                 -- the invoke_agent span this grades
    label_id INTEGER REFERENCES labels(id),
    description TEXT NOT NULL DEFAULT '',
    annotator TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    UNIQUE(project_id, trace_id, span_id)
);
```

One `projects` row is seeded on first run from settings (`top_level_agent_name =
'rx_assistant_agent'`), with a starter label set (`Pass`, `Neutral`, `Fail`), both editable
afterward through the UI. There is no "add project" UI in v1 — a new source project would
need its own read token minted and wired into settings (see Settings below), which is a
config/deploy change, not a runtime one; the projects table exists so criteria/labels/
annotations have somewhere to live per project once that's supported, not to make onboarding
self-serve today.

## Logfire Integration

### Reading interactions

Uses the `logfire` package's own first-party client — `logfire.experimental.query_client.
AsyncLogfireQueryClient` — rather than hand-rolled HTTP calls. This is the same client the
official Logfire MCP server is built on. It takes a **read token** (a genuinely distinct
token type from the write token `logfire.configure()` uses — the client calls
`/v1/read-token-info`, which only resolves for read tokens) and derives the correct
regional base URL from the token itself:

```python
from logfire.experimental.query_client import AsyncLogfireQueryClient

async with AsyncLogfireQueryClient(settings.rx_assistant_read_token) as client:
    info = await client.info()  # {'organization_name': ..., 'project_name': ...}
    result = await client.query_json_rows(
        sql,
        min_timestamp=fourteen_days_ago,
        max_timestamp=cursor,   # None on first page, else the oldest start_timestamp seen so far
        limit=page_size,
    )
    rows = result["rows"]
```

`min_timestamp`/`max_timestamp` bound the query server-side (max range is 14 days); paging
"load more" is done by re-querying with `max_timestamp` set to the oldest `start_timestamp`
already shown, `ORDER BY start_timestamp DESC` in the SQL. No filter UI in v1 — always
most-recent-first.

### Interaction identification

Real trace data pulled from `rx-assistant-demo` during design confirms the shape: each chat
turn is `POST /api/chat` → `invoke_agent rx_assistant_agent` (the interaction graded here) →
nested `execute_tool delegate_task` → `invoke_agent rx_assistant_web_research_agent` (a
sub-agent, not graded) → various `chat {model}`/`execute_tool` spans. The query targets only
the top-level span:

```sql
SELECT trace_id, span_id, start_timestamp, duration, attributes
FROM records
WHERE span_name = 'invoke_agent ' || :top_level_agent_name
ORDER BY start_timestamp DESC
```

`query_json_rows()` takes a plain SQL string with no parameter binding — `:top_level_agent_name`
above is Python-side string interpolation, not a driver placeholder. Since that value comes
from the project's stored `top_level_agent_name` (editable through this app's own UI), it's
validated against `^[A-Za-z0-9_]+$` on save and rejected otherwise — the query
interpolation is otherwise a SQL-injection surface into Logfire's query engine.

### Extracting input/output

From the matched span's `attributes`:

- `pydantic_ai.all_messages` — JSON array of `{role, parts}` messages for the full
  conversation up to and including this turn.
- `pydantic_ai.new_message_index` — index into `all_messages` where this turn's new messages
  begin.
- `final_result` — the agent's final text output for this turn, when not redacted by
  Logfire's scrubbing.

Parsing rule:
- **Input** = the text content of the last `role: user` message with index
  `< new_message_index`.
- **Output** = `final_result` if present and non-empty; otherwise the text content of the
  last `role: assistant` message at index `>= new_message_index`.
- **Full conversation** (optional expand within the expanded interaction) = every message in
  `all_messages`, rendered as a simple transcript (including tool calls/tool results) for
  reviewers who want turn context.
- Scrubbed values (Logfire returns literal strings like `"[Scrubbed due to 'auth']"`) are
  rendered as-is — a faithful representation of what the trace contains, no special handling.
- If `all_messages` is missing or fails to parse as expected, fall back to showing the span's
  raw `attributes` JSON so nothing is silently hidden.

### Trace link

Each interaction links to the trace in the Logfire UI, built the same way the official
Logfire MCP server's `logfire_link` tool does — from the read token's own `info()` plus the
query client's `base_url` (same host serves both the query API and the UI):

```python
url = f"{client.base_url}/{info['organization_name']}/{info['project_name']}?q=trace_id='{trace_id}'"
```

No separate org-name setting is needed; it comes from the token itself.

## Out of Scope

- **Write-back onto the source trace.** `AsyncLogfireQueryClient` only does reads; writing an
  annotation onto the original `rx-assistant-demo` trace would need a *second*,
  separately-minted write token for that project (on top of the read token above), which
  isn't worth the extra credential to manage for v1 — annotations live only in this app's own
  SQLite. If this is wanted later, the mechanism is proven out: construct a remote
  `SpanContext` from the stored `trace_id`/`span_id` (standard OTel context propagation, the
  same trick used to continue a trace across a network boundary via `traceparent`), attach it
  as the current context, then call `logfire.info(...)` under a `rx-assistant`-scoped write
  token — the new log lands as a child entry on that trace's timeline.
- **Logfire's native annotation queue.** It's a gated Design-Partner/early-access feature
  with no documented public API for reading queue items or writing verdicts into it.
  `annotation-studio` is its own system of record, not an integration with that feature.
- **`agent-tracing` and `chat-demo` projects.** Only `rx-assistant-demo`'s trace shape has
  been confirmed; adding another project means confirming it also uses `invoke_agent {name}`
  spans with the same attributes, or giving it its own extraction rule.
- **No offline evals suite** (unlike `chat`/`rx-assistant` — there's no agent here to
  evaluate against a `pydantic_evals.Dataset`).
- **No production deployment, auth, or secrets manager** (repo-wide convention).

## Architecture

```
apps/annotation-studio/
  pyproject.toml
  .env.example
  Dockerfile
  frontend/                          # React + TS + Vite, this app's own deviation
    package.json
    vite.config.ts
    src/
  src/annotation_studio/
    __init__.py
    main.py                          # create_annotation_studio_app() factory
    settings.py                      # SourceSettings, AppSettings (see below)
    db.py                            # stdlib sqlite3 access
    logfire_client.py                # AsyncLogfireQueryClient wrapper + message parsing
    routes.py                        # FastAPI routes, mounts built frontend as static files
    static/dist/                     # built frontend output (gitignored; built in Docker/dev)
  tests/
```

- `main.py` calls `demo_core.logfire_setup.configure_logfire("annotation-studio", ...)` for
  self-instrumentation (its own FastAPI requests, its own SQLite queries — into a **separate,
  dedicated** `annotation-studio` Logfire project, not `rx-assistant-demo`), then
  `demo_core.web.create_app("annotation-studio")` for the FastAPI app + `/health` +
  standard error handling. `configure_logfire` unconditionally calls
  `instrument_pydantic_ai()`/`instrument_system_metrics()`; both are harmless no-ops here
  since this app runs no agent — reused as-is per the repo's copy-the-pattern convention.
- `routes.py` mounts `src/annotation_studio/static/dist` (the built React app) via FastAPI's
  `StaticFiles`, alongside the `/api/*` JSON routes.

## API

- `GET /api/projects` — list projects (v1: always the one seeded row).
- `GET /api/projects/{id}` — project detail incl. criteria + labels.
- `PUT /api/projects/{id}` — update `criteria_text` and/or labels.
- `GET /api/projects/{id}/interactions?cursor=` — fetch a page of interactions from Logfire,
  merged with any existing local annotation for each.
- `PUT /api/projects/{id}/annotations/{trace_id}/{span_id}` — upsert an annotation
  (`label_id`, `description`, `annotator`).

## Frontend

React + TypeScript + Vite, `react-markdown` for input/output rendering, React Router for
navigation.

- **Project list** (`/`) — cards for each project (v1: just `rx-assistant`).
- **Project detail** (`/projects/:id`) — top: criteria edit/save textarea (explicit Save
  button, not autosave). Label-set editor below it (add/remove/reorder labels). Main:
  paginated list of interaction rows (timestamp, truncated input preview, current label badge
  if graded). Clicking a row expands it in place to show:
  - Input (rendered markdown)
  - Output (rendered markdown)
  - "View full conversation" toggle (raw transcript, including tool calls)
  - Label picker (from the project's labels)
  - Description textarea with explicit Save
  - "Open trace in Logfire ↗" link (new tab)

### Frontend build (deviation from repo convention)

Every other demo here is server-rendered Jinja with no JS build step; this is the first with
one, so the Dockerfile needs a multi-stage build not covered by the `add-demo` skill's
standard template:

```dockerfile
FROM node:20-slim AS frontend-build
WORKDIR /app/apps/annotation-studio/frontend
COPY apps/annotation-studio/frontend/package.json apps/annotation-studio/frontend/package-lock.json ./
RUN npm ci
COPY apps/annotation-studio/frontend ./
RUN npm run build

FROM python:3.11-slim
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/demo_core ./packages/demo_core
COPY apps/annotation-studio ./apps/annotation-studio
COPY --from=frontend-build /app/apps/annotation-studio/frontend/dist \
     ./apps/annotation-studio/src/annotation_studio/static/dist
RUN uv sync --frozen --package annotation-studio

EXPOSE 8000
CMD ["uv", "run", "--package", "annotation-studio", "uvicorn", \
     "annotation_studio.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Local (non-Docker) dev runs two processes: `cd apps/annotation-studio/frontend && npm install
&& npm run dev` (Vite dev server, proxying `/api` to `http://localhost:8000`) alongside `uv
run --package annotation-studio uvicorn annotation_studio.main:app --reload`. A "does the real
built bundle work" check is `npm run build` once, then load everything from the FastAPI port
directly.

## Settings

```python
class SourceSettings(BaseSettings):
    """Read-only access to rx-assistant's Logfire project."""
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)
    read_token: str = Field(validation_alias="RX_ASSISTANT_LOGFIRE_READ_TOKEN")
    top_level_agent_name: str = Field(default="rx_assistant_agent")


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)
    database_path: str = Field(default="data/annotation_studio.sqlite3", validation_alias="ANNOTATION_STUDIO_DATABASE_PATH")
    default_annotator: str = Field(default="", validation_alias="ANNOTATION_STUDIO_DEFAULT_ANNOTATOR")
```

`demo_core.settings.LogfireSettings` (bare `LOGFIRE_TOKEN`) is reused as-is for
self-instrumentation, same as every other demo — no naming collision risk here since each app
has its own `.env` file, unlike a hypothetical shared root `.env`.

`.env.example`:

```
# Self-instrumentation only (this app's own FastAPI/SQLite traces). Mint a fresh
# write token for a new dedicated 'annotation-studio' Logfire project — do not
# reuse rx-assistant's token here.
LOGFIRE_TOKEN=

# Read-only access to rx-assistant's Logfire project, minted separately from
# rx-assistant's own LOGFIRE_TOKEN (apps/rx-assistant/.env is not shared).
RX_ASSISTANT_LOGFIRE_READ_TOKEN=

ANNOTATION_STUDIO_DATABASE_PATH=data/annotation_studio.sqlite3
ANNOTATION_STUDIO_DEFAULT_ANNOTATOR=
```

No `PYDANTIC_AI_GATEWAY_API_KEY` — deviates from the `add-demo` skill's usual minimum since
this app makes no model calls of its own.

## Docker Compose

One new service (no database container needed — SQLite is a bind-mounted/named-volume file,
no infra monitoring collector needed — nothing here is a container worth monitoring beyond
what `configure_logfire`'s `instrument_system_metrics()` already reports):

```yaml
annotation-studio:
  build:
    context: .
    dockerfile: apps/annotation-studio/Dockerfile
  env_file: apps/annotation-studio/.env
  volumes:
    - annotation_studio_data:/app/apps/annotation-studio/data
  ports:
    - "8003:8000"
  profiles: ["annotation-studio", "all"]

volumes:
  annotation_studio_data:
```

## Dependencies (`apps/annotation-studio/pyproject.toml`)

`demo-core`, `logfire` (declared explicitly since `logfire.experimental.query_client` is
imported directly, even though `demo-core` already pulls `logfire` in transitively — per
`AGENTS.md`'s rule), `fastapi`, `uvicorn[standard]`, `python-dotenv`. No `pydantic-ai`, no
`pydantic-evals`, no `jinja2` (static files instead of server templates).

## Testing

- Backend: `pytest`, mirroring `rx-assistant`'s pattern — a `conftest.py` force-setting dummy
  `LOGFIRE_TOKEN`, `RX_ASSISTANT_LOGFIRE_READ_TOKEN`, `LOGFIRE_SEND_TO_LOGFIRE=false`, and a
  temp-file `ANNOTATION_STUDIO_DATABASE_PATH` at module level. No `tests/__init__.py`.
  - Unit tests for the message-parsing logic (input/output extraction from
    `pydantic_ai.all_messages`, using fixture JSON captured from a real `rx-assistant-demo`
    span during design).
  - Route tests via FastAPI's `TestClient`, monkeypatching the `logfire_client` query
    function (the same way `rx-assistant`'s tests monkeypatch `_query_conditions`) to return
    canned rows — the default suite never calls the real Logfire query API.
- Frontend: no component-level test suite for v1 given the small surface area; `npm run
  build` succeeding is the CI-relevant check, manual in-browser verification is the bar for
  UI behavior, per this repo's existing testing norms.

Run `uv sync --all-packages` after adding the workspace member, then `uv run pytest
apps/annotation-studio/tests/` and `docker compose --profile annotation-studio config` before
committing, per the `add-demo` skill's checklist.
