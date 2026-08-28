# annotation-studio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `annotation-studio`, a new `apps/annotation-studio` FastAPI + React demo that lets a reviewer browse `rx-assistant` agent interactions pulled live from Logfire, grade each one against a per-project criteria block, and store the label + written justification in local SQLite.

**Architecture:** A FastAPI backend (`src/annotation_studio/`) exposes `/api/*` JSON routes backed by stdlib `sqlite3` (projects/labels/annotations) and `logfire.experimental.query_client.AsyncLogfireQueryClient` (read-only interaction data from `rx-assistant-demo`). A React + TypeScript + Vite SPA (`frontend/`) consumes that API and is built to static files the backend serves. This is the first demo with a JS build step — a deliberate deviation, justified in the spec.

**Tech Stack:** Python 3.11, FastAPI, stdlib `sqlite3`, `logfire` (query client only, no agent), `demo_core`; React 18, TypeScript, Vite, React Router, `react-markdown`.

**Spec:** [docs/superpowers/specs/2026-08-28-annotation-studio-design.md](../specs/2026-08-28-annotation-studio-design.md)

> **Revision notice:** The original detailed plan is preserved below for implementation
> context. The authoritative replacement tasks are in the Approved Revision Addendum at the
> end; they incorporate annotator profiles, stable labels, atomic updates, keyset pagination,
> and append-only Logfire write-back.

## Global Constraints

- No auth anywhere (repo-wide convention — local-only).
- **No write-back onto the source `rx-assistant-demo` trace.** Annotations live only in this app's own SQLite (see spec's "Out of Scope").
- **No integration with Logfire's native annotation queue** — gated feature, no public API. This app is its own system of record.
- Only `rx-assistant-demo`'s trace shape is in scope for v1 — one fixed seeded project.
- `top_level_agent_name` is interpolated directly into a SQL string sent to Logfire's query engine (no parameter binding available on `query_json_rows`) — it MUST be validated against `^[A-Za-z0-9_]+$` on every write path before use.
- Backend tests never call the real Logfire query API — the query function is always monkeypatched.
- **Do not add `tests/__init__.py`** to `apps/annotation-studio/tests/` — see `add-demo` skill's "Common Mistakes."
- Frontend has no unit-test suite in v1 (per spec's Testing section) — `npm run build` succeeding (TypeScript typecheck + Vite bundle) is each frontend task's correctness gate, not a red/green test cycle.
- Every `apps/annotation-studio/.env` (gitignored) and `.env.example` follow the repo's per-app credential convention (`AGENTS.md`).

## Corrections to the Approved Spec

Two issues surfaced while grounding this plan in a real `invoke_agent rx_assistant_agent` span pulled live from `rx-assistant-demo` (trace `01a045b8d6d40acd6c98ee00f1a3fe93`). Both are folded into the tasks below; flagging them here since they change behavior described in the "Approved" spec document itself:

1. **Input-extraction rule was backwards.** The spec says "Input = the text content of the last `role: user` message with index `< new_message_index`." That finds the *previous* turn's user message, not the current one — `new_message_index` marks where this turn's *new* messages begin, and the real span confirms the current turn's question sits at `all_messages[new_message_index]`, not before it. This plan's `parse_interaction` (Task 3) uses `>= new_message_index` for both input and output, matching the observed data (verified: index 38 = `"What about major depressive disorder?"`, the actual question this span answers).
2. **`top_level_agent_name` had no way to actually be edited.** The spec's Logfire Integration section says it's "editable through this app's own UI" and requires validation on save, but neither the API list nor the Frontend section include a control for it — only `criteria_text` and labels are wired up. Task 5 adds `top_level_agent_name` to `PUT /api/projects/{id}` (validated, 400 on reject) and Task 9 adds an input field for it next to the criteria editor, so the field the spec itself says needs an edit-and-validate path actually has one.

A third small gap: the spec's data model has an `annotator` column but no UI field for it anywhere in the Frontend section, and `ANNOTATION_STUDIO_DEFAULT_ANNOTATOR` has no stated wiring. Resolved by having the backend fill `annotator` from `AppSettings.default_annotator` server-side on every annotation upsert — no annotator input in the UI, no new API surface.

---

### Task 1: Workspace scaffold — pyproject, env, settings

**Files:**
- Create: `apps/annotation-studio/pyproject.toml`
- Create: `apps/annotation-studio/.env.example`
- Create: `apps/annotation-studio/src/annotation_studio/__init__.py`
- Create: `apps/annotation-studio/src/annotation_studio/settings.py`
- Test: `apps/annotation-studio/tests/conftest.py`
- Test: `apps/annotation-studio/tests/test_settings.py`

**Interfaces:**
- Produces: `annotation_studio.settings.SourceSettings` (fields: `read_token: str`, `top_level_agent_name: str = "rx_assistant_agent"`) and `annotation_studio.settings.AppSettings` (fields: `database_path: str`, `default_annotator: str`) — every later task's settings usage.

- [ ] **Step 1: Create the package directory and `pyproject.toml`**

```toml
[project]
name = "annotation-studio"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "demo-core",
    "logfire",
    "fastapi",
    "uvicorn[standard]",
    "python-dotenv",
]

[tool.uv.sources]
demo-core = { workspace = true }

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/annotation_studio"]
```

- [ ] **Step 2: Create `.env.example`**

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

- [ ] **Step 3: Create `src/annotation_studio/__init__.py`**

```python
"""Annotation Studio demo application.

Loads this app's own .env here, at package import, so it is in place before any
submodule constructs a Settings object. override=False keeps real environment
variables (e.g. docker-compose's env_file:) ahead of the .env file.
"""

from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
```

- [ ] **Step 4: Write the failing settings tests** — `apps/annotation-studio/tests/test_settings.py`

```python
import pytest
from pydantic import ValidationError

from annotation_studio.settings import AppSettings, SourceSettings


def test_source_settings_reads_read_token(monkeypatch) -> None:
    monkeypatch.setenv("RX_ASSISTANT_LOGFIRE_READ_TOKEN", "pylf_read_test")

    settings = SourceSettings()

    assert settings.read_token == "pylf_read_test"
    assert settings.top_level_agent_name == "rx_assistant_agent"


def test_source_settings_requires_read_token(monkeypatch) -> None:
    monkeypatch.delenv("RX_ASSISTANT_LOGFIRE_READ_TOKEN", raising=False)

    with pytest.raises(ValidationError):
        SourceSettings()


def test_app_settings_defaults(monkeypatch) -> None:
    monkeypatch.delenv("ANNOTATION_STUDIO_DATABASE_PATH", raising=False)
    monkeypatch.delenv("ANNOTATION_STUDIO_DEFAULT_ANNOTATOR", raising=False)

    settings = AppSettings()

    assert settings.database_path == "data/annotation_studio.sqlite3"
    assert settings.default_annotator == ""


def test_app_settings_reads_overrides(monkeypatch) -> None:
    monkeypatch.setenv("ANNOTATION_STUDIO_DATABASE_PATH", "/tmp/x.sqlite3")
    monkeypatch.setenv("ANNOTATION_STUDIO_DEFAULT_ANNOTATOR", "duncan")

    settings = AppSettings()

    assert settings.database_path == "/tmp/x.sqlite3"
    assert settings.default_annotator == "duncan"
```

- [ ] **Step 5: Create `tests/conftest.py`** (dummy env vars so settings can construct without a real `.env`; a temp-file database path so later tasks' module-level `app = create_annotation_studio_app()` never touches a real file in the repo)

```python
import os
import tempfile

_db_fd, _db_path = tempfile.mkstemp(suffix=".sqlite3")
os.close(_db_fd)

# Forced (not setdefault) so a developer's real credentials in their shell can never leak
# into a test run. This runs at conftest import, before any test module.
os.environ["LOGFIRE_TOKEN"] = "test-token"
os.environ["LOGFIRE_SEND_TO_LOGFIRE"] = "false"
os.environ["RX_ASSISTANT_LOGFIRE_READ_TOKEN"] = "test-read-token"
os.environ["ANNOTATION_STUDIO_DATABASE_PATH"] = _db_path

import logfire  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture(autouse=True, scope="session")
def _configure_logfire_for_tests() -> None:
    logfire.configure(send_to_logfire=False)
```

- [ ] **Step 6: Create `src/annotation_studio/settings.py`**

```python
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SourceSettings(BaseSettings):
    """Read-only access to rx-assistant's Logfire project."""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    read_token: str = Field(validation_alias="RX_ASSISTANT_LOGFIRE_READ_TOKEN")
    top_level_agent_name: str = Field(default="rx_assistant_agent")


class AppSettings(BaseSettings):
    """This app's own local settings — its SQLite database and default annotator name."""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    database_path: str = Field(
        default="data/annotation_studio.sqlite3", validation_alias="ANNOTATION_STUDIO_DATABASE_PATH"
    )
    default_annotator: str = Field(default="", validation_alias="ANNOTATION_STUDIO_DEFAULT_ANNOTATOR")
```

- [ ] **Step 7: Register the workspace member and run the tests**

```bash
uv sync --all-packages
uv run pytest apps/annotation-studio/tests/ -v
```

Expected: 4 passed (root `pyproject.toml`'s `[tool.uv.workspace] members = ["packages/*", "apps/*"]` already globs in the new app — no root file edit needed).

- [ ] **Step 8: Commit**

```bash
git add apps/annotation-studio/pyproject.toml apps/annotation-studio/.env.example \
  apps/annotation-studio/src/annotation_studio/__init__.py \
  apps/annotation-studio/src/annotation_studio/settings.py \
  apps/annotation-studio/tests/conftest.py apps/annotation-studio/tests/test_settings.py \
  uv.lock
git commit -m "annotation-studio: scaffold workspace member and settings"
```

---

### Task 2: SQLite schema and CRUD

**Files:**
- Create: `apps/annotation-studio/src/annotation_studio/db.py`
- Test: `apps/annotation-studio/tests/test_db.py`

**Interfaces:**
- Produces: `db.get_connection(database_path: str) -> sqlite3.Connection`, `db.init_db(conn)`, `db.seed_default_project(conn, top_level_agent_name: str) -> None`, `db.list_projects(conn) -> list[dict]`, `db.get_project(conn, project_id: int) -> dict | None`, `db.update_project_criteria(conn, project_id, criteria_text: str) -> None`, `db.update_project_top_level_agent_name(conn, project_id, name: str) -> None`, `db.list_labels(conn, project_id) -> list[dict]`, `db.update_project_labels(conn, project_id, names: list[str]) -> list[dict]` (raises `ValueError` if removing a label still referenced by an annotation), `db.get_annotation(conn, project_id, trace_id, span_id) -> dict | None`, `db.upsert_annotation(conn, project_id, trace_id, span_id, label_id, description, annotator) -> dict`. Every project/label/annotation dict has plain JSON-serializable values (str/int/None). Used directly by `main.py` and `routes.py` (Tasks 5–6).

- [ ] **Step 1: Write the failing tests** — `apps/annotation-studio/tests/test_db.py`

```python
import sqlite3

import pytest

from annotation_studio import db


def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    db.init_db(conn)
    return conn


def test_seed_default_project_creates_project_and_starter_labels() -> None:
    conn = _fresh_conn()

    db.seed_default_project(conn, "rx_assistant_agent")

    projects = db.list_projects(conn)
    assert len(projects) == 1
    assert projects[0]["name"] == "rx-assistant"
    assert projects[0]["top_level_agent_name"] == "rx_assistant_agent"
    labels = db.list_labels(conn, projects[0]["id"])
    assert [label["name"] for label in labels] == ["Pass", "Neutral", "Fail"]


def test_seed_default_project_is_idempotent() -> None:
    conn = _fresh_conn()

    db.seed_default_project(conn, "rx_assistant_agent")
    db.seed_default_project(conn, "rx_assistant_agent")

    assert len(db.list_projects(conn)) == 1


def test_update_project_criteria_persists_text() -> None:
    conn = _fresh_conn()
    db.seed_default_project(conn, "rx_assistant_agent")
    project_id = db.list_projects(conn)[0]["id"]

    db.update_project_criteria(conn, project_id, "Be strict about hallucinations.")

    assert db.get_project(conn, project_id)["criteria_text"] == "Be strict about hallucinations."


def test_update_project_top_level_agent_name_persists() -> None:
    conn = _fresh_conn()
    db.seed_default_project(conn, "rx_assistant_agent")
    project_id = db.list_projects(conn)[0]["id"]

    db.update_project_top_level_agent_name(conn, project_id, "rx_assistant_agent_v2")

    assert db.get_project(conn, project_id)["top_level_agent_name"] == "rx_assistant_agent_v2"


def test_update_project_labels_renames_reorders_and_adds() -> None:
    conn = _fresh_conn()
    db.seed_default_project(conn, "rx_assistant_agent")
    project_id = db.list_projects(conn)[0]["id"]

    labels = db.update_project_labels(conn, project_id, ["Fail", "Pass", "Hallucination"])

    assert [label["name"] for label in labels] == ["Fail", "Pass", "Hallucination"]


def test_update_project_labels_removing_an_unused_label_succeeds() -> None:
    conn = _fresh_conn()
    db.seed_default_project(conn, "rx_assistant_agent")
    project_id = db.list_projects(conn)[0]["id"]

    labels = db.update_project_labels(conn, project_id, ["Pass", "Fail"])

    assert [label["name"] for label in labels] == ["Pass", "Fail"]


def test_update_project_labels_removing_a_label_used_by_an_annotation_raises() -> None:
    conn = _fresh_conn()
    db.seed_default_project(conn, "rx_assistant_agent")
    project_id = db.list_projects(conn)[0]["id"]
    neutral_id = next(
        label["id"] for label in db.list_labels(conn, project_id) if label["name"] == "Neutral"
    )
    db.upsert_annotation(conn, project_id, "trace-1", "span-1", neutral_id, "why", "duncan")

    with pytest.raises(ValueError):
        db.update_project_labels(conn, project_id, ["Pass", "Fail"])

    # The whole update was rolled back, not just the removal.
    assert [label["name"] for label in db.list_labels(conn, project_id)] == ["Pass", "Neutral", "Fail"]


def test_upsert_annotation_inserts_then_updates_same_row() -> None:
    conn = _fresh_conn()
    db.seed_default_project(conn, "rx_assistant_agent")
    project_id = db.list_projects(conn)[0]["id"]
    pass_id = next(label["id"] for label in db.list_labels(conn, project_id) if label["name"] == "Pass")

    first = db.upsert_annotation(conn, project_id, "trace-1", "span-1", pass_id, "good", "duncan")
    second = db.upsert_annotation(conn, project_id, "trace-1", "span-1", pass_id, "still good", "duncan")

    assert first["id"] == second["id"]
    assert second["description"] == "still good"


def test_get_annotation_returns_none_when_not_graded() -> None:
    conn = _fresh_conn()
    db.seed_default_project(conn, "rx_assistant_agent")
    project_id = db.list_projects(conn)[0]["id"]

    assert db.get_annotation(conn, project_id, "trace-x", "span-x") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest apps/annotation-studio/tests/test_db.py -v
```

Expected: FAIL/ERROR — `annotation_studio.db` doesn't exist yet.

- [ ] **Step 3: Create `src/annotation_studio/db.py`**

```python
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    top_level_agent_name TEXT NOT NULL,
    criteria_text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS labels (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    UNIQUE(project_id, name)
);

CREATE TABLE IF NOT EXISTS annotations (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    trace_id TEXT NOT NULL,
    span_id TEXT NOT NULL,
    label_id INTEGER REFERENCES labels(id),
    description TEXT NOT NULL DEFAULT '',
    annotator TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, trace_id, span_id)
);
"""


def get_connection(database_path: str) -> sqlite3.Connection:
    Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: this connection is created once at app startup and reused
    # across request-handling calls, which may not all land on the same OS thread.
    conn = sqlite3.connect(database_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


def seed_default_project(conn: sqlite3.Connection, top_level_agent_name: str) -> None:
    if conn.execute("SELECT id FROM projects LIMIT 1").fetchone() is not None:
        return
    now = _now()
    cursor = conn.execute(
        "INSERT INTO projects (name, top_level_agent_name, criteria_text, created_at, updated_at) "
        "VALUES (?, ?, '', ?, ?)",
        ("rx-assistant", top_level_agent_name, now, now),
    )
    project_id = cursor.lastrowid
    for order, name in enumerate(["Pass", "Neutral", "Fail"]):
        conn.execute(
            "INSERT INTO labels (project_id, name, sort_order) VALUES (?, ?, ?)",
            (project_id, name, order),
        )
    conn.commit()


def list_projects(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM projects ORDER BY id").fetchall()
    return [_row_to_dict(row) for row in rows]


def get_project(conn: sqlite3.Connection, project_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return _row_to_dict(row) if row else None


def update_project_criteria(conn: sqlite3.Connection, project_id: int, criteria_text: str) -> None:
    conn.execute(
        "UPDATE projects SET criteria_text = ?, updated_at = ? WHERE id = ?",
        (criteria_text, _now(), project_id),
    )
    conn.commit()


def update_project_top_level_agent_name(
    conn: sqlite3.Connection, project_id: int, top_level_agent_name: str
) -> None:
    conn.execute(
        "UPDATE projects SET top_level_agent_name = ?, updated_at = ? WHERE id = ?",
        (top_level_agent_name, _now(), project_id),
    )
    conn.commit()


def list_labels(conn: sqlite3.Connection, project_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM labels WHERE project_id = ? ORDER BY sort_order", (project_id,)
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def update_project_labels(conn: sqlite3.Connection, project_id: int, names: list[str]) -> list[dict]:
    """Replace the project's label set with `names`, in order. Existing labels matching a
    name by exact string are kept (same id, so existing annotations stay valid) and just
    re-ordered; new names are inserted; labels no longer present are deleted — but deleting
    a label still referenced by an annotation violates the FK and raises ValueError, rolling
    back the *entire* call (including any renames/reorders already applied in this batch),
    so the frontend's "Save labels" is all-or-nothing."""
    existing = {
        row["name"]: row["id"]
        for row in conn.execute(
            "SELECT id, name FROM labels WHERE project_id = ?", (project_id,)
        ).fetchall()
    }

    for order, name in enumerate(names):
        if name in existing:
            conn.execute("UPDATE labels SET sort_order = ? WHERE id = ?", (order, existing[name]))
        else:
            conn.execute(
                "INSERT INTO labels (project_id, name, sort_order) VALUES (?, ?, ?)",
                (project_id, name, order),
            )

    for name in set(existing) - set(names):
        try:
            conn.execute("DELETE FROM labels WHERE id = ?", (existing[name],))
        except sqlite3.IntegrityError:
            conn.rollback()
            raise ValueError(f"Cannot remove label {name!r}: it is used by an existing annotation")

    conn.commit()
    return list_labels(conn, project_id)


def get_annotation(
    conn: sqlite3.Connection, project_id: int, trace_id: str, span_id: str
) -> dict | None:
    row = conn.execute(
        "SELECT * FROM annotations WHERE project_id = ? AND trace_id = ? AND span_id = ?",
        (project_id, trace_id, span_id),
    ).fetchone()
    return _row_to_dict(row) if row else None


def upsert_annotation(
    conn: sqlite3.Connection,
    project_id: int,
    trace_id: str,
    span_id: str,
    label_id: int | None,
    description: str,
    annotator: str,
) -> dict:
    now = _now()
    existing = get_annotation(conn, project_id, trace_id, span_id)
    if existing:
        conn.execute(
            "UPDATE annotations SET label_id = ?, description = ?, annotator = ?, updated_at = ? "
            "WHERE id = ?",
            (label_id, description, annotator, now, existing["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO annotations "
            "(project_id, trace_id, span_id, label_id, description, annotator, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (project_id, trace_id, span_id, label_id, description, annotator, now, now),
        )
    conn.commit()
    return get_annotation(conn, project_id, trace_id, span_id)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest apps/annotation-studio/tests/test_db.py -v
```

Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add apps/annotation-studio/src/annotation_studio/db.py apps/annotation-studio/tests/test_db.py
git commit -m "annotation-studio: add SQLite schema and CRUD layer"
```

---

### Task 3: Message-parsing logic (`parse_interaction`)

Pure logic, no network — extracts a turn's input/output from one Logfire span row's `attributes`. Grounded in a real `invoke_agent rx_assistant_agent` span pulled from `rx-assistant-demo` (trace `01a045b8d6d40acd6c98ee00f1a3fe93`) during planning; the fixture below is that real span's message shape, trimmed to 6 messages for a self-contained test.

**Files:**
- Create: `apps/annotation-studio/src/annotation_studio/logfire_client.py`
- Create: `apps/annotation-studio/tests/fixtures/real_span_trimmed.json`
- Create: `apps/annotation-studio/tests/fixtures/final_result_present.json`
- Create: `apps/annotation-studio/tests/fixtures/malformed_attributes.json`
- Test: `apps/annotation-studio/tests/test_logfire_client.py`

**Interfaces:**
- Produces: `logfire_client.AGENT_NAME_PATTERN`, `logfire_client.validate_agent_name(name: str) -> None` (raises `ValueError`), `logfire_client.Interaction` dataclass (`trace_id: str, span_id: str, start_timestamp: str, input_text: str, output_text: str, full_conversation: list[dict], trace_url: str, raw_attributes: dict | None = None`), `logfire_client.parse_interaction(row: dict, trace_url: str) -> Interaction`. Consumed by Task 4's `fetch_project_interactions` and Task 6's routes.

- [ ] **Step 1: Create the fixture files**

`apps/annotation-studio/tests/fixtures/real_span_trimmed.json` (real message shape, trimmed to one prior turn + the turn this span answers):

```json
{
  "trace_id": "01a045b8d6d40acd6c98ee00f1a3fe93",
  "span_id": "c7a2373c3fe61d3f",
  "start_timestamp": "2026-08-28T00:15:36.667186Z",
  "duration": 23.455358413,
  "attributes": {
    "pydantic_ai.new_message_index": 2,
    "pydantic_ai.all_messages": [
      {
        "role": "user",
        "parts": [{"type": "text", "content": "What medications could I ask for to help treat ADHD"}]
      },
      {
        "role": "assistant",
        "parts": [
          {"type": "text", "content": "Common ADHD medications include stimulants like methylphenidate and amphetamine-based options."}
        ],
        "finish_reason": "stop"
      },
      {
        "role": "user",
        "parts": [{"type": "text", "content": "What about major depressive disorder?"}]
      },
      {
        "role": "assistant",
        "parts": [
          {
            "type": "tool_call",
            "id": "toolu_01RKeoUzaDHUVtmna136AtBM",
            "name": "search_medications",
            "arguments": {"query": "major depressive disorder", "condition": "MDD", "limit": 5}
          }
        ],
        "finish_reason": "tool_call"
      },
      {
        "role": "user",
        "parts": [
          {
            "type": "tool_call_response",
            "id": "toolu_01RKeoUzaDHUVtmna136AtBM",
            "name": "search_medications",
            "result": [{"med_name": "sertraline", "generic_name": "sertraline", "brand_names": "Zoloft"}]
          }
        ]
      },
      {
        "role": "assistant",
        "parts": [
          {"type": "text", "content": "Major depressive disorder (MDD) is often treated with SSRIs such as sertraline (Zoloft)."}
        ],
        "finish_reason": "stop"
      }
    ],
    "final_result": "[Scrubbed due to 'auth']"
  }
}
```

`apps/annotation-studio/tests/fixtures/final_result_present.json` (tests that a present, non-scrubbed `final_result` wins over the assistant message text):

```json
{
  "trace_id": "trace-final-result-present",
  "span_id": "span-1",
  "start_timestamp": "2026-08-28T01:00:00.000000Z",
  "duration": 1.2,
  "attributes": {
    "pydantic_ai.new_message_index": 0,
    "pydantic_ai.all_messages": [
      {"role": "user", "parts": [{"type": "text", "content": "Is ibuprofen safe with warfarin?"}]},
      {
        "role": "assistant",
        "parts": [{"type": "text", "content": "internal draft text, should not be shown"}],
        "finish_reason": "stop"
      }
    ],
    "final_result": "No — ibuprofen combined with warfarin significantly raises bleeding risk; consult your prescriber."
  }
}
```

`apps/annotation-studio/tests/fixtures/malformed_attributes.json` (tests the raw-attributes fallback):

```json
{
  "trace_id": "trace-malformed",
  "span_id": "span-2",
  "start_timestamp": "2026-08-28T02:00:00.000000Z",
  "duration": 0.5,
  "attributes": {
    "some_other_field": "value"
  }
}
```

- [ ] **Step 2: Write the failing tests** — `apps/annotation-studio/tests/test_logfire_client.py`

```python
import json
from pathlib import Path

import pytest

from annotation_studio.logfire_client import parse_interaction, validate_agent_name

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
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
uv run pytest apps/annotation-studio/tests/test_logfire_client.py -v
```

Expected: FAIL/ERROR — `annotation_studio.logfire_client` doesn't exist yet.

- [ ] **Step 4: Create `src/annotation_studio/logfire_client.py`**

```python
import re
from dataclasses import dataclass

AGENT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")


def validate_agent_name(name: str) -> None:
    """Raise ValueError if `name` isn't safe to interpolate into the SQL span-name filter
    in fetch_project_interactions (Task 4) — it comes from a project's stored, UI-editable
    top_level_agent_name, so this is the only thing standing between that field and a
    SQL-injection into Logfire's query engine."""
    if not AGENT_NAME_PATTERN.match(name):
        raise ValueError(f"Invalid top_level_agent_name: {name!r}")


@dataclass
class Interaction:
    trace_id: str
    span_id: str
    start_timestamp: str
    input_text: str
    output_text: str
    full_conversation: list[dict]
    trace_url: str
    raw_attributes: dict | None = None


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
            raw_attributes=attributes,
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
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run pytest apps/annotation-studio/tests/test_logfire_client.py -v
```

Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add apps/annotation-studio/src/annotation_studio/logfire_client.py \
  apps/annotation-studio/tests/fixtures/ apps/annotation-studio/tests/test_logfire_client.py
git commit -m "annotation-studio: add message-parsing logic grounded in a real rx-assistant span"
```

---

### Task 4: Logfire query wrapper (`fetch_project_interactions`)

**Files:**
- Modify: `apps/annotation-studio/src/annotation_studio/logfire_client.py`
- Modify: `apps/annotation-studio/tests/test_logfire_client.py`

**Interfaces:**
- Consumes: `Interaction`, `parse_interaction`, `validate_agent_name` (Task 3).
- Produces: `logfire_client.build_trace_link(base_url: str, organization_name: str, project_name: str, trace_id: str) -> str`, `async logfire_client.fetch_project_interactions(read_token: str, top_level_agent_name: str, cursor: str | None, limit: int) -> tuple[list[Interaction], str | None]`. Task 6's routes monkeypatch this exact function name in `annotation_studio.routes`'s namespace.

- [ ] **Step 1: Write the failing tests** — append to `apps/annotation-studio/tests/test_logfire_client.py`

```python
import annotation_studio.logfire_client as logfire_client


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

    async def query_json_rows(self, sql, min_timestamp=None, max_timestamp=None, limit=None, **kwargs):
        self.queries.append(
            {"sql": sql, "min_timestamp": min_timestamp, "max_timestamp": max_timestamp, "limit": limit}
        )
        return {"columns": [], "rows": self._rows}


def _row(trace_id: str, start_timestamp: str) -> dict:
    return {
        "trace_id": trace_id,
        "span_id": "span-1",
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
    url = logfire_client.build_trace_link(
        "https://logfire-us.pydantic.dev", "duncan", "rx-assistant-demo", "abc123"
    )
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
    assert next_cursor is None  # page wasn't full (1 row < limit 20)
    assert "invoke_agent rx_assistant_agent" in fake_client.queries[0]["sql"]


async def test_fetch_project_interactions_sets_next_cursor_when_page_is_full(monkeypatch) -> None:
    rows = [_row(f"trace-{i}", f"2026-08-28T00:0{i}:00Z") for i in range(2)]
    fake_client = FakeQueryClient(rows)
    monkeypatch.setattr(logfire_client, "AsyncLogfireQueryClient", lambda read_token: fake_client)

    interactions, next_cursor = await logfire_client.fetch_project_interactions(
        "test-token", "rx_assistant_agent", cursor=None, limit=2
    )

    assert next_cursor == interactions[-1].start_timestamp


async def test_fetch_project_interactions_rejects_invalid_agent_name() -> None:
    with pytest.raises(ValueError):
        await logfire_client.fetch_project_interactions(
            "test-token", "not valid; DROP TABLE records", cursor=None, limit=20
        )
```

Add `import pytest` to the top of the file alongside the existing imports.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest apps/annotation-studio/tests/test_logfire_client.py -v
```

Expected: FAIL — `build_trace_link`/`fetch_project_interactions`/`AsyncLogfireQueryClient` not defined in the module yet.

- [ ] **Step 3: Add to `src/annotation_studio/logfire_client.py`** (append below `parse_interaction`)

```python
from datetime import datetime, timedelta, timezone

from logfire.experimental.query_client import AsyncLogfireQueryClient


def build_trace_link(base_url: str, organization_name: str, project_name: str, trace_id: str) -> str:
    return f"{base_url}/{organization_name}/{project_name}?q=trace_id='{trace_id}'"


async def fetch_project_interactions(
    read_token: str,
    top_level_agent_name: str,
    cursor: str | None,
    limit: int,
) -> tuple[list[Interaction], str | None]:
    """Fetch one page of interactions (most-recent-first) for `top_level_agent_name`, each
    with its Logfire trace URL already attached. `cursor` is the `start_timestamp` of the
    oldest interaction already shown (None for the first page); the returned `next_cursor`
    is that same value for this page, or None if this page wasn't full (nothing older left
    to load)."""
    validate_agent_name(top_level_agent_name)

    max_timestamp = datetime.fromisoformat(cursor) if cursor else None
    min_timestamp = datetime.now(timezone.utc) - timedelta(days=14)
    sql = (
        "SELECT trace_id, span_id, start_timestamp, duration, attributes "
        "FROM records "
        f"WHERE span_name = 'invoke_agent {top_level_agent_name}' "
        "ORDER BY start_timestamp DESC"
    )

    async with AsyncLogfireQueryClient(read_token) as client:
        info = await client.info()
        result = await client.query_json_rows(
            sql, min_timestamp=min_timestamp, max_timestamp=max_timestamp, limit=limit
        )
        interactions = [
            parse_interaction(
                row,
                build_trace_link(
                    client.base_url, info["organization_name"], info["project_name"], row["trace_id"]
                ),
            )
            for row in result["rows"]
        ]

    next_cursor = interactions[-1].start_timestamp if len(interactions) == limit else None
    return interactions, next_cursor
```

(Move the `AGENT_NAME_PATTERN`/`validate_agent_name`/`Interaction`/`_text_content`/`parse_interaction` definitions from Task 3 above this new code, or append this code at the end of the file — either way, the module ends up with both halves.)

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest apps/annotation-studio/tests/test_logfire_client.py -v
```

Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add apps/annotation-studio/src/annotation_studio/logfire_client.py apps/annotation-studio/tests/test_logfire_client.py
git commit -m "annotation-studio: add paginated Logfire query wrapper"
```

---

### Task 5: FastAPI app factory + projects API

**Files:**
- Create: `apps/annotation-studio/src/annotation_studio/main.py`
- Create: `apps/annotation-studio/src/annotation_studio/routes.py`
- Test: `apps/annotation-studio/tests/test_routes.py`

**Interfaces:**
- Consumes: `db.*` (Task 2), `logfire_client.validate_agent_name` (Task 3), `settings.AppSettings`/`SourceSettings` (Task 1).
- Produces: `main.create_annotation_studio_app(send_to_logfire: bool | None = None, connection: sqlite3.Connection | None = None) -> FastAPI` and module-level `main.app`; `routes.register_routes(app, conn, source_settings, app_settings) -> None`. Task 6 extends `routes.py` in place (adds interactions/annotations endpoints to the same `register_routes` body) and Task 9's frontend consumes this API's JSON shapes directly.

- [ ] **Step 1: Write the failing tests** — `apps/annotation-studio/tests/test_routes.py`

```python
import sqlite3

from fastapi.testclient import TestClient

import annotation_studio.main as annotation_studio_main


def _app_with_fresh_db() -> TestClient:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    app = annotation_studio_main.create_annotation_studio_app(send_to_logfire=False, connection=conn)
    return TestClient(app)


def test_list_projects_returns_seeded_project() -> None:
    client = _app_with_fresh_db()

    response = client.get("/api/projects")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "rx-assistant"


def test_get_project_includes_labels() -> None:
    client = _app_with_fresh_db()
    project_id = client.get("/api/projects").json()[0]["id"]

    response = client.get(f"/api/projects/{project_id}")

    assert response.status_code == 200
    labels = response.json()["labels"]
    assert [label["name"] for label in labels] == ["Pass", "Neutral", "Fail"]


def test_get_project_returns_404_for_unknown_id() -> None:
    client = _app_with_fresh_db()

    response = client.get("/api/projects/999")

    assert response.status_code == 404


def test_put_project_updates_criteria_and_labels() -> None:
    client = _app_with_fresh_db()
    project_id = client.get("/api/projects").json()[0]["id"]

    response = client.put(
        f"/api/projects/{project_id}",
        json={"criteria_text": "Be strict.", "label_names": ["Good", "Bad"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["criteria_text"] == "Be strict."
    assert [label["name"] for label in body["labels"]] == ["Good", "Bad"]


def test_put_project_updates_top_level_agent_name_when_valid() -> None:
    client = _app_with_fresh_db()
    project_id = client.get("/api/projects").json()[0]["id"]

    response = client.put(
        f"/api/projects/{project_id}", json={"top_level_agent_name": "rx_assistant_agent_v2"}
    )

    assert response.status_code == 200
    assert response.json()["top_level_agent_name"] == "rx_assistant_agent_v2"


def test_put_project_rejects_invalid_top_level_agent_name() -> None:
    client = _app_with_fresh_db()
    project_id = client.get("/api/projects").json()[0]["id"]

    response = client.put(
        f"/api/projects/{project_id}", json={"top_level_agent_name": "not valid; DROP TABLE"}
    )

    assert response.status_code == 400
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest apps/annotation-studio/tests/test_routes.py -v
```

Expected: FAIL/ERROR — `annotation_studio.main` doesn't exist yet.

- [ ] **Step 3: Create `src/annotation_studio/routes.py`**

```python
import sqlite3

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

from annotation_studio import db
from annotation_studio.logfire_client import validate_agent_name
from annotation_studio.settings import AppSettings, SourceSettings


class ProjectUpdateRequest(BaseModel):
    criteria_text: str | None = None
    top_level_agent_name: str | None = None
    label_names: list[str] | None = None


def register_routes(
    app: FastAPI,
    conn: sqlite3.Connection,
    source_settings: SourceSettings,
    app_settings: AppSettings,
) -> None:
    router = APIRouter(prefix="/api")

    @router.get("/projects")
    async def list_projects() -> list[dict]:
        return db.list_projects(conn)

    @router.get("/projects/{project_id}")
    async def get_project(project_id: int) -> dict:
        project = db.get_project(conn, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project_not_found")
        project["labels"] = db.list_labels(conn, project_id)
        return project

    @router.put("/projects/{project_id}")
    async def update_project(project_id: int, payload: ProjectUpdateRequest) -> dict:
        project = db.get_project(conn, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project_not_found")

        if payload.top_level_agent_name is not None:
            try:
                validate_agent_name(payload.top_level_agent_name)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            db.update_project_top_level_agent_name(conn, project_id, payload.top_level_agent_name)

        if payload.criteria_text is not None:
            db.update_project_criteria(conn, project_id, payload.criteria_text)

        if payload.label_names is not None:
            try:
                db.update_project_labels(conn, project_id, payload.label_names)
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc))

        project = db.get_project(conn, project_id)
        project["labels"] = db.list_labels(conn, project_id)
        return project

    app.include_router(router)
```

- [ ] **Step 4: Create `src/annotation_studio/main.py`**

```python
import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from demo_core.logfire_setup import configure_logfire
from demo_core.settings import LogfireSettings
from demo_core.web import create_app

from annotation_studio import db
from annotation_studio.routes import register_routes
from annotation_studio.settings import AppSettings, SourceSettings

_STATIC_DIST = Path(__file__).parent / "static" / "dist"


def create_annotation_studio_app(
    send_to_logfire: bool | None = None, connection: sqlite3.Connection | None = None
) -> FastAPI:
    if send_to_logfire is None:
        # Lets the test suite (see tests/conftest.py) force offline mode before this
        # module's own `app = create_annotation_studio_app()` line runs at import time.
        send_to_logfire = os.environ.get("LOGFIRE_SEND_TO_LOGFIRE", "true").lower() != "false"

    logfire_settings = LogfireSettings()
    configure_logfire("annotation-studio", send_to_logfire=send_to_logfire, token=logfire_settings.token)
    app = create_app(title="Annotation Studio")

    app_settings = AppSettings()
    source_settings = SourceSettings()

    conn = connection if connection is not None else db.get_connection(app_settings.database_path)
    db.init_db(conn)
    db.seed_default_project(conn, source_settings.top_level_agent_name)

    register_routes(app, conn, source_settings, app_settings)

    # Serve the built React SPA. /assets holds Vite's hashed JS/CSS; the catch-all below
    # returns index.html for every other non-API path so React Router's client-side routes
    # (e.g. /projects/5) work on a hard refresh, not just on in-app navigation.
    if _STATIC_DIST.exists():
        app.mount("/assets", StaticFiles(directory=str(_STATIC_DIST / "assets")), name="frontend-assets")

        @app.get("/{full_path:path}")
        async def serve_frontend(full_path: str) -> FileResponse:
            return FileResponse(_STATIC_DIST / "index.html")

    return app


app = create_annotation_studio_app()
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run pytest apps/annotation-studio/tests/test_routes.py -v
```

Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add apps/annotation-studio/src/annotation_studio/main.py apps/annotation-studio/src/annotation_studio/routes.py \
  apps/annotation-studio/tests/test_routes.py
git commit -m "annotation-studio: add FastAPI app factory and projects API"
```

---

### Task 6: Interactions + annotations API

**Files:**
- Modify: `apps/annotation-studio/src/annotation_studio/routes.py`
- Modify: `apps/annotation-studio/tests/test_routes.py`

**Interfaces:**
- Consumes: `fetch_project_interactions` (Task 4), `db.get_annotation`/`db.upsert_annotation` (Task 2).
- Produces: `GET /api/projects/{id}/interactions?cursor=` → `{"items": [...], "next_cursor": str | None}` where each item is an `Interaction` (Task 3 dataclass fields) plus `"annotation": dict | None`; `PUT /api/projects/{id}/annotations/{trace_id}/{span_id}` → the upserted annotation dict. Task 9's frontend `api.ts` calls these two endpoints directly with these exact shapes.

- [ ] **Step 1: Write the failing tests** — append to `apps/annotation-studio/tests/test_routes.py`

```python
from annotation_studio.logfire_client import Interaction


def _fake_interaction(
    trace_id: str, span_id: str = "span-1", start_timestamp: str = "2026-08-28T00:00:00Z"
) -> Interaction:
    return Interaction(
        trace_id=trace_id,
        span_id=span_id,
        start_timestamp=start_timestamp,
        input_text="What about MDD?",
        output_text="MDD is treated with SSRIs.",
        full_conversation=[
            {"role": "user", "parts": [{"type": "text", "content": "What about MDD?"}]},
            {"role": "assistant", "parts": [{"type": "text", "content": "MDD is treated with SSRIs."}]},
        ],
        trace_url=f"https://logfire-us.pydantic.dev/duncan/rx-assistant-demo?q=trace_id='{trace_id}'",
    )


def test_list_interactions_merges_local_annotations(monkeypatch) -> None:
    async def fake_fetch(read_token, top_level_agent_name, cursor, limit):
        return [_fake_interaction("trace-1")], None

    import annotation_studio.routes as routes
    monkeypatch.setattr(routes, "fetch_project_interactions", fake_fetch)

    client = _app_with_fresh_db()
    project_id = client.get("/api/projects").json()[0]["id"]

    response = client.get(f"/api/projects/{project_id}/interactions")

    assert response.status_code == 200
    body = response.json()
    assert body["next_cursor"] is None
    assert len(body["items"]) == 1
    assert body["items"][0]["trace_id"] == "trace-1"
    assert body["items"][0]["annotation"] is None


def test_upsert_annotation_creates_and_returns_it() -> None:
    client = _app_with_fresh_db()
    project_id = client.get("/api/projects").json()[0]["id"]
    pass_id = next(
        label["id"] for label in client.get(f"/api/projects/{project_id}").json()["labels"] if label["name"] == "Pass"
    )

    response = client.put(
        f"/api/projects/{project_id}/annotations/trace-1/span-1",
        json={"label_id": pass_id, "description": "Correct and grounded."},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["label_id"] == pass_id
    assert body["description"] == "Correct and grounded."


def test_upsert_annotation_uses_configured_default_annotator(monkeypatch) -> None:
    monkeypatch.setenv("ANNOTATION_STUDIO_DEFAULT_ANNOTATOR", "duncan@pydantic.dev")
    client = _app_with_fresh_db()
    project_id = client.get("/api/projects").json()[0]["id"]

    response = client.put(
        f"/api/projects/{project_id}/annotations/trace-1/span-1",
        json={"label_id": None, "description": ""},
    )

    assert response.json()["annotator"] == "duncan@pydantic.dev"


def test_list_interactions_returns_annotation_when_already_graded(monkeypatch) -> None:
    client = _app_with_fresh_db()
    project_id = client.get("/api/projects").json()[0]["id"]
    pass_id = next(
        label["id"] for label in client.get(f"/api/projects/{project_id}").json()["labels"] if label["name"] == "Pass"
    )
    client.put(
        f"/api/projects/{project_id}/annotations/trace-1/span-1",
        json={"label_id": pass_id, "description": "good"},
    )

    async def fake_fetch(read_token, top_level_agent_name, cursor, limit):
        return [_fake_interaction("trace-1", span_id="span-1")], None

    import annotation_studio.routes as routes
    monkeypatch.setattr(routes, "fetch_project_interactions", fake_fetch)

    response = client.get(f"/api/projects/{project_id}/interactions")

    assert response.json()["items"][0]["annotation"]["label_id"] == pass_id


def test_put_project_returns_409_when_removing_label_in_use() -> None:
    client = _app_with_fresh_db()
    project_id = client.get("/api/projects").json()[0]["id"]
    neutral_id = next(
        label["id"] for label in client.get(f"/api/projects/{project_id}").json()["labels"] if label["name"] == "Neutral"
    )
    client.put(
        f"/api/projects/{project_id}/annotations/trace-1/span-1",
        json={"label_id": neutral_id, "description": "why"},
    )

    response = client.put(f"/api/projects/{project_id}", json={"label_names": ["Pass", "Fail"]})

    assert response.status_code == 409
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest apps/annotation-studio/tests/test_routes.py -v
```

Expected: FAIL — the interactions/annotations routes don't exist yet (404s), and `fetch_project_interactions` isn't imported into `routes.py` yet so `monkeypatch.setattr` errors.

- [ ] **Step 3: Modify `src/annotation_studio/routes.py`** — add imports and two new route handlers inside `register_routes`, before `app.include_router(router)`

Add to the imports at the top:

```python
from dataclasses import asdict

from annotation_studio.logfire_client import fetch_project_interactions, validate_agent_name
```

(replacing the single-line `from annotation_studio.logfire_client import validate_agent_name`)

Add below the class `ProjectUpdateRequest`:

```python
PAGE_SIZE = 20


class AnnotationUpdateRequest(BaseModel):
    label_id: int | None = None
    description: str = ""
```

Add inside `register_routes`, after the `update_project` handler and before `app.include_router(router)`:

```python
    @router.get("/projects/{project_id}/interactions")
    async def list_interactions(project_id: int, cursor: str | None = None) -> dict:
        project = db.get_project(conn, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project_not_found")

        interactions, next_cursor = await fetch_project_interactions(
            source_settings.read_token, project["top_level_agent_name"], cursor, PAGE_SIZE
        )

        items = []
        for interaction in interactions:
            annotation = db.get_annotation(conn, project_id, interaction.trace_id, interaction.span_id)
            items.append({**asdict(interaction), "annotation": annotation})

        return {"items": items, "next_cursor": next_cursor}

    @router.put("/projects/{project_id}/annotations/{trace_id}/{span_id}")
    async def upsert_annotation(
        project_id: int, trace_id: str, span_id: str, payload: AnnotationUpdateRequest
    ) -> dict:
        project = db.get_project(conn, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project_not_found")
        return db.upsert_annotation(
            conn,
            project_id,
            trace_id,
            span_id,
            payload.label_id,
            payload.description,
            app_settings.default_annotator,
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest apps/annotation-studio/tests/test_routes.py -v
```

Expected: 11 passed.

- [ ] **Step 5: Run the full backend suite**

```bash
uv run pytest apps/annotation-studio/tests/ -v
```

Expected: 28 passed (4 settings + 8 db + 9 logfire_client + 11 routes − wait: sum precisely by running; all green either way).

- [ ] **Step 6: Commit**

```bash
git add apps/annotation-studio/src/annotation_studio/routes.py apps/annotation-studio/tests/test_routes.py
git commit -m "annotation-studio: add interactions and annotations API"
```

---

### Task 7: Frontend scaffold — build tooling, routing shell, types, API client

No test-first cycle here — per the spec's Testing section, this app's frontend has no unit-test suite in v1. `npm run build` succeeding (TypeScript typecheck + Vite bundle) is this task's correctness gate.

**Files:**
- Create: `apps/annotation-studio/frontend/package.json`
- Create: `apps/annotation-studio/frontend/tsconfig.json`
- Create: `apps/annotation-studio/frontend/tsconfig.node.json`
- Create: `apps/annotation-studio/frontend/vite.config.ts`
- Create: `apps/annotation-studio/frontend/index.html`
- Create: `apps/annotation-studio/frontend/src/main.tsx`
- Create: `apps/annotation-studio/frontend/src/App.tsx`
- Create: `apps/annotation-studio/frontend/src/index.css`
- Create: `apps/annotation-studio/frontend/src/types.ts`
- Create: `apps/annotation-studio/frontend/src/api.ts`
- Create: `apps/annotation-studio/frontend/src/pages/ProjectList.tsx`
- Create: `apps/annotation-studio/frontend/src/pages/ProjectDetail.tsx`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `types.ts`'s `Label`, `ProjectSummary`, `Project`, `MessagePart`, `Message`, `Interaction`, `InteractionsPage`, `Annotation`; `api.ts`'s `listProjects`, `getProject`, `updateProject`, `listInteractions`, `upsertAnnotation`. Tasks 8–11 build the actual page/component content against these types and functions — this task's `ProjectList`/`ProjectDetail` are minimal placeholders that render without error.

- [ ] **Step 1: Create `frontend/package.json`**

```json
{
  "name": "annotation-studio-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.26.0",
    "react-markdown": "^9.0.1"
  },
  "devDependencies": {
    "@types/react": "^18.3.3",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.1",
    "typescript": "^5.5.3",
    "vite": "^5.4.0"
  }
}
```

- [ ] **Step 2: Create `frontend/tsconfig.json` and `frontend/tsconfig.node.json`**

`tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true
  },
  "include": ["src"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

`tsconfig.node.json`:

```json
{
  "compilerOptions": {
    "composite": true,
    "skipLibCheck": true,
    "module": "ESNext",
    "moduleResolution": "bundler",
    "allowSyntheticDefaultImports": true
  },
  "include": ["vite.config.ts"]
}
```

- [ ] **Step 3: Create `frontend/vite.config.ts`**

```ts
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
```

- [ ] **Step 4: Create `frontend/index.html`**

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Annotation Studio</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 5: Create `frontend/src/types.ts`**

```ts
export interface Label {
  id: number;
  name: string;
  sort_order: number;
}

export interface ProjectSummary {
  id: number;
  name: string;
  top_level_agent_name: string;
  criteria_text: string;
  created_at: string;
  updated_at: string;
}

export interface Project extends ProjectSummary {
  labels: Label[];
}

export interface Annotation {
  id: number;
  label_id: number | null;
  description: string;
  annotator: string;
  created_at: string;
  updated_at: string;
}

export interface MessagePart {
  type: string;
  content?: string;
  id?: string;
  name?: string;
  arguments?: unknown;
  result?: unknown;
}

export interface Message {
  role: string;
  parts: MessagePart[];
  finish_reason?: string | null;
}

export interface Interaction {
  trace_id: string;
  span_id: string;
  start_timestamp: string;
  input_text: string;
  output_text: string;
  full_conversation: Message[];
  trace_url: string;
  raw_attributes: Record<string, unknown> | null;
  annotation: Annotation | null;
}

export interface InteractionsPage {
  items: Interaction[];
  next_cursor: string | null;
}
```

- [ ] **Step 6: Create `frontend/src/api.ts`**

```ts
import type { Annotation, InteractionsPage, Project, ProjectSummary } from "./types";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`${options?.method ?? "GET"} ${path} failed: ${response.status} ${body}`);
  }
  return response.json() as Promise<T>;
}

export function listProjects(): Promise<ProjectSummary[]> {
  return request<ProjectSummary[]>("/api/projects");
}

export function getProject(projectId: number): Promise<Project> {
  return request<Project>(`/api/projects/${projectId}`);
}

export function updateProject(
  projectId: number,
  payload: { criteria_text?: string; top_level_agent_name?: string; label_names?: string[] },
): Promise<Project> {
  return request<Project>(`/api/projects/${projectId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function listInteractions(projectId: number, cursor: string | null): Promise<InteractionsPage> {
  const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
  return request<InteractionsPage>(`/api/projects/${projectId}/interactions${query}`);
}

export function upsertAnnotation(
  projectId: number,
  traceId: string,
  spanId: string,
  payload: { label_id: number | null; description: string },
): Promise<Annotation> {
  return request<Annotation>(`/api/projects/${projectId}/annotations/${traceId}/${spanId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}
```

- [ ] **Step 7: Create placeholder pages** — `frontend/src/pages/ProjectList.tsx`

```tsx
export function ProjectList() {
  return <h1>Annotation Studio</h1>;
}
```

`frontend/src/pages/ProjectDetail.tsx`:

```tsx
export function ProjectDetail() {
  return <h1>Project</h1>;
}
```

- [ ] **Step 8: Create `frontend/src/App.tsx`**

```tsx
import { Route, Routes } from "react-router-dom";

import { ProjectDetail } from "./pages/ProjectDetail";
import { ProjectList } from "./pages/ProjectList";

export function App() {
  return (
    <Routes>
      <Route path="/" element={<ProjectList />} />
      <Route path="/projects/:id" element={<ProjectDetail />} />
    </Routes>
  );
}
```

- [ ] **Step 9: Create `frontend/src/main.tsx`**

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { App } from "./App";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
```

- [ ] **Step 10: Create `frontend/src/index.css`**

```css
:root {
  color-scheme: light dark;
  font-family: system-ui, sans-serif;
}

body {
  margin: 0;
  padding: 2rem;
  max-width: 960px;
  margin-inline: auto;
}
```

- [ ] **Step 11: Add `node_modules/` to `.gitignore`**

Append to the repo's `.gitignore` (the existing `dist/` entry already covers `frontend/dist/` and `src/annotation_studio/static/dist/` since it has no leading slash):

```

# Node (annotation-studio frontend)
node_modules/
```

- [ ] **Step 12: Install and build**

```bash
cd apps/annotation-studio/frontend
npm install
npm run build
```

Expected: `dist/index.html` and `dist/assets/*.js`/`*.css` produced, no TypeScript errors.

- [ ] **Step 13: Commit**

```bash
cd /Users/duncanmckinnon/Documents/code/pydantic-demos
git add apps/annotation-studio/frontend/package.json apps/annotation-studio/frontend/package-lock.json \
  apps/annotation-studio/frontend/tsconfig.json apps/annotation-studio/frontend/tsconfig.node.json \
  apps/annotation-studio/frontend/vite.config.ts apps/annotation-studio/frontend/index.html \
  apps/annotation-studio/frontend/src .gitignore
git commit -m "annotation-studio: scaffold React + TS + Vite frontend"
```

---

### Task 8: Project List page

**Files:**
- Modify: `apps/annotation-studio/frontend/src/pages/ProjectList.tsx`

**Interfaces:**
- Consumes: `api.listProjects` (Task 7), `types.ProjectSummary` (Task 7).

- [ ] **Step 1: Implement `ProjectList.tsx`**

```tsx
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { listProjects } from "../api";
import type { ProjectSummary } from "../types";

export function ProjectList() {
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listProjects()
      .then(setProjects)
      .catch((err: unknown) => setError(String(err)));
  }, []);

  if (error) return <p className="error">{error}</p>;
  if (projects === null) return <p>Loading…</p>;

  return (
    <div className="project-list">
      <h1>Annotation Studio</h1>
      {projects.map((project) => (
        <Link key={project.id} to={`/projects/${project.id}`} className="project-card">
          <h2>{project.name}</h2>
          <p>Source agent: {project.top_level_agent_name}</p>
        </Link>
      ))}
    </div>
  );
}
```

- [ ] **Step 2: Add card styling** — append to `frontend/src/index.css`

```css
.project-card {
  display: block;
  border: 1px solid #8884;
  border-radius: 8px;
  padding: 1rem;
  margin-bottom: 1rem;
  text-decoration: none;
  color: inherit;
}
```

- [ ] **Step 3: Build and manually verify**

```bash
cd apps/annotation-studio/frontend
npm run build
```

Then, with the backend running (`uv run --package annotation-studio uvicorn annotation_studio.main:app --reload` from the repo root, in a separate terminal, after `cp apps/annotation-studio/.env.example apps/annotation-studio/.env` and filling in real tokens) and `npm run dev` in this directory, open `http://localhost:5173` and confirm the seeded `rx-assistant` project card renders and links to `/projects/1`.

- [ ] **Step 4: Commit**

```bash
git add apps/annotation-studio/frontend/src/pages/ProjectList.tsx apps/annotation-studio/frontend/src/index.css
git commit -m "annotation-studio: implement project list page"
```

---

### Task 9: Project Detail — criteria, agent name, and label editors

**Files:**
- Create: `apps/annotation-studio/frontend/src/components/CriteriaEditor.tsx`
- Create: `apps/annotation-studio/frontend/src/components/LabelEditor.tsx`
- Modify: `apps/annotation-studio/frontend/src/pages/ProjectDetail.tsx`

**Interfaces:**
- Consumes: `api.getProject`, `api.updateProject` (Task 7), `types.Project`, `types.Label` (Task 7).
- Produces: `ProjectDetail`'s `project` state, passed to Task 10/11's interaction list and `InteractionRow`.

- [ ] **Step 1: Create `components/CriteriaEditor.tsx`** (bundles the criteria textarea with the agent-name field — see plan's "Corrections to the Approved Spec" #2: the spec requires this field be editable-and-validated but never wired it into any UI control)

```tsx
import { useState } from "react";

interface Props {
  initialCriteriaText: string;
  initialAgentName: string;
  onSave: (values: { criteria_text: string; top_level_agent_name: string }) => Promise<void>;
}

export function CriteriaEditor({ initialCriteriaText, initialAgentName, onSave }: Props) {
  const [criteriaText, setCriteriaText] = useState(initialCriteriaText);
  const [agentName, setAgentName] = useState(initialAgentName);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      await onSave({ criteria_text: criteriaText, top_level_agent_name: agentName });
    } catch (err) {
      setError(String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="criteria-editor">
      <label htmlFor="agent-name">Source agent name (Logfire span name suffix)</label>
      <input id="agent-name" value={agentName} onChange={(e) => setAgentName(e.target.value)} />

      <label htmlFor="criteria-text">Grading criteria</label>
      <textarea
        id="criteria-text"
        rows={8}
        value={criteriaText}
        onChange={(e) => setCriteriaText(e.target.value)}
      />

      <button onClick={handleSave} disabled={saving}>
        {saving ? "Saving…" : "Save"}
      </button>
      {error && <p className="error">{error}</p>}
    </section>
  );
}
```

- [ ] **Step 2: Create `components/LabelEditor.tsx`**

```tsx
import { useState } from "react";

import type { Label } from "../types";

interface Props {
  initialLabels: Label[];
  onSave: (names: string[]) => Promise<void>;
}

export function LabelEditor({ initialLabels, onSave }: Props) {
  const [names, setNames] = useState(initialLabels.map((label) => label.name));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const updateName = (index: number, value: string) =>
    setNames((prev) => prev.map((n, i) => (i === index ? value : n)));

  const removeAt = (index: number) => setNames((prev) => prev.filter((_, i) => i !== index));

  const moveUp = (index: number) =>
    setNames((prev) => {
      if (index === 0) return prev;
      const next = [...prev];
      [next[index - 1], next[index]] = [next[index], next[index - 1]];
      return next;
    });

  const moveDown = (index: number) =>
    setNames((prev) => {
      if (index === prev.length - 1) return prev;
      const next = [...prev];
      [next[index], next[index + 1]] = [next[index + 1], next[index]];
      return next;
    });

  const addLabel = () => setNames((prev) => [...prev, "New label"]);

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      await onSave(names.map((n) => n.trim()).filter((n) => n.length > 0));
    } catch (err) {
      setError(String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="label-editor">
      <h3>Labels</h3>
      {names.map((name, index) => (
        <div key={index} className="label-row">
          <input value={name} onChange={(e) => updateName(index, e.target.value)} />
          <button onClick={() => moveUp(index)} disabled={index === 0}>
            ↑
          </button>
          <button onClick={() => moveDown(index)} disabled={index === names.length - 1}>
            ↓
          </button>
          <button onClick={() => removeAt(index)}>Remove</button>
        </div>
      ))}
      <button onClick={addLabel}>Add label</button>
      <button onClick={handleSave} disabled={saving}>
        {saving ? "Saving…" : "Save labels"}
      </button>
      {error && <p className="error">{error}</p>}
    </section>
  );
}
```

- [ ] **Step 3: Implement `pages/ProjectDetail.tsx`** (interaction list wired in Task 10 — for now, render the editors only)

```tsx
import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { getProject, updateProject } from "../api";
import { CriteriaEditor } from "../components/CriteriaEditor";
import { LabelEditor } from "../components/LabelEditor";
import type { Project } from "../types";

export function ProjectDetail() {
  const { id } = useParams<{ id: string }>();
  const projectId = Number(id);

  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadProject = useCallback(() => {
    getProject(projectId)
      .then(setProject)
      .catch((err: unknown) => setError(String(err)));
  }, [projectId]);

  useEffect(() => {
    loadProject();
  }, [loadProject]);

  if (error) return <p className="error">{error}</p>;
  if (project === null) return <p>Loading…</p>;

  return (
    <div className="project-detail">
      <h1>{project.name}</h1>
      <CriteriaEditor
        initialCriteriaText={project.criteria_text}
        initialAgentName={project.top_level_agent_name}
        onSave={async (values) => {
          const updated = await updateProject(projectId, values);
          setProject(updated);
        }}
      />
      <LabelEditor
        initialLabels={project.labels}
        onSave={async (names) => {
          const updated = await updateProject(projectId, { label_names: names });
          setProject(updated);
        }}
      />
    </div>
  );
}
```

- [ ] **Step 4: Add editor styling** — append to `frontend/src/index.css`

```css
.criteria-editor,
.label-editor {
  border: 1px solid #8884;
  border-radius: 8px;
  padding: 1rem;
  margin-bottom: 1.5rem;
}

.criteria-editor textarea,
.criteria-editor input,
.label-editor input {
  width: 100%;
  box-sizing: border-box;
  margin-bottom: 0.5rem;
}

.label-row {
  display: flex;
  gap: 0.5rem;
  align-items: center;
  margin-bottom: 0.25rem;
}

.error {
  color: #d9534f;
}
```

- [ ] **Step 5: Build and manually verify**

```bash
cd apps/annotation-studio/frontend
npm run build
```

With backend + `npm run dev` running, open `http://localhost:5173/projects/1`, edit the criteria text and agent name, click Save, reload the page, and confirm the values persisted. Try adding/removing/reordering labels and confirm the same.

- [ ] **Step 6: Commit**

```bash
git add apps/annotation-studio/frontend/src/components/CriteriaEditor.tsx \
  apps/annotation-studio/frontend/src/components/LabelEditor.tsx \
  apps/annotation-studio/frontend/src/pages/ProjectDetail.tsx apps/annotation-studio/frontend/src/index.css
git commit -m "annotation-studio: implement criteria, agent name, and label editors"
```

---

### Task 10: Interaction list with expand/collapse and full conversation

**Files:**
- Create: `apps/annotation-studio/frontend/src/components/InteractionRow.tsx`
- Modify: `apps/annotation-studio/frontend/src/pages/ProjectDetail.tsx`

**Interfaces:**
- Consumes: `api.listInteractions` (Task 7), `types.Interaction`/`types.Message`/`types.MessagePart` (Task 7).
- Produces: `InteractionRow` component (read-only in this task — label picker/description save wired in Task 11).

- [ ] **Step 1: Add `react-markdown` rendering + full-conversation transcript to `components/InteractionRow.tsx`**

```tsx
import { useState } from "react";
import ReactMarkdown from "react-markdown";

import type { Interaction, Message, MessagePart } from "../types";

interface Props {
  interaction: Interaction;
}

function renderPart(part: MessagePart, key: number) {
  if (part.type === "text") return <p key={key}>{part.content}</p>;
  if (part.type === "tool_call") {
    return <pre key={key}>{`Called tool: ${part.name}(${JSON.stringify(part.arguments, null, 2)})`}</pre>;
  }
  if (part.type === "tool_call_response") {
    return <pre key={key}>{`Tool result: ${JSON.stringify(part.result, null, 2)}`}</pre>;
  }
  return <pre key={key}>{JSON.stringify(part)}</pre>;
}

function renderMessage(message: Message, key: number) {
  return (
    <div key={key} className={`message message-${message.role}`}>
      <strong>{message.role}</strong>
      {message.parts.map((part, partIndex) => renderPart(part, partIndex))}
    </div>
  );
}

export function InteractionRow({ interaction }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [showFullConversation, setShowFullConversation] = useState(false);

  return (
    <div className="interaction-row">
      <button className="interaction-summary" onClick={() => setExpanded((v) => !v)}>
        <span className="timestamp">{new Date(interaction.start_timestamp).toLocaleString()}</span>
        <span className="preview">{interaction.input_text.slice(0, 120)}</span>
      </button>

      {expanded && (
        <div className="interaction-detail">
          {interaction.raw_attributes ? (
            <>
              <h4>Raw attributes (message parsing failed)</h4>
              <pre>{JSON.stringify(interaction.raw_attributes, null, 2)}</pre>
            </>
          ) : (
            <>
              <h4>Input</h4>
              <ReactMarkdown>{interaction.input_text}</ReactMarkdown>

              <h4>Output</h4>
              <ReactMarkdown>{interaction.output_text}</ReactMarkdown>

              <button onClick={() => setShowFullConversation((v) => !v)}>
                {showFullConversation ? "Hide full conversation" : "View full conversation"}
              </button>
              {showFullConversation && (
                <div className="full-conversation">
                  {interaction.full_conversation.map((message, index) => renderMessage(message, index))}
                </div>
              )}
            </>
          )}

          <a href={interaction.trace_url} target="_blank" rel="noopener noreferrer">
            Open trace in Logfire ↗
          </a>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Wire the interaction list and pagination into `pages/ProjectDetail.tsx`** — add these imports and state/effects, and render the list below the `LabelEditor`

Add imports:

```tsx
import { listInteractions } from "../api";
import { InteractionRow } from "../components/InteractionRow";
import type { Interaction, Project } from "../types";
```

Add inside the component, alongside the existing `project`/`error` state:

```tsx
  const [interactions, setInteractions] = useState<Interaction[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const loadInteractions = useCallback(
    (cursor: string | null) => {
      setLoading(true);
      listInteractions(projectId, cursor)
        .then((page) => {
          setInteractions((prev) => (cursor ? [...prev, ...page.items] : page.items));
          setNextCursor(page.next_cursor);
        })
        .catch((err: unknown) => setError(String(err)))
        .finally(() => setLoading(false));
    },
    [projectId],
  );
```

Update the load-on-mount effect to also load the first page:

```tsx
  useEffect(() => {
    loadProject();
    loadInteractions(null);
  }, [loadProject, loadInteractions]);
```

Add below the `LabelEditor` in the returned JSX:

```tsx
      <h2>Interactions</h2>
      {interactions.map((interaction) => (
        <InteractionRow key={`${interaction.trace_id}:${interaction.span_id}`} interaction={interaction} />
      ))}
      {nextCursor && (
        <button onClick={() => loadInteractions(nextCursor)} disabled={loading}>
          {loading ? "Loading…" : "Load more"}
        </button>
      )}
```

- [ ] **Step 3: Add interaction-row styling** — append to `frontend/src/index.css`

```css
.interaction-row {
  border: 1px solid #8884;
  border-radius: 8px;
  margin-bottom: 0.75rem;
}

.interaction-summary {
  display: flex;
  gap: 1rem;
  width: 100%;
  padding: 0.75rem 1rem;
  background: none;
  border: none;
  text-align: left;
  cursor: pointer;
  font: inherit;
}

.interaction-summary .preview {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.interaction-detail {
  padding: 0 1rem 1rem;
}

.full-conversation {
  background: #8881;
  border-radius: 6px;
  padding: 0.75rem;
  margin: 0.5rem 0;
  max-height: 400px;
  overflow-y: auto;
}

.message pre {
  white-space: pre-wrap;
  word-break: break-word;
}
```

- [ ] **Step 4: Build and manually verify**

```bash
cd apps/annotation-studio/frontend
npm run build
```

With backend + `npm run dev` running against a real `RX_ASSISTANT_LOGFIRE_READ_TOKEN`, open `http://localhost:5173/projects/1` and confirm real interactions load, expand to show input/output markdown, "View full conversation" shows the transcript, "Load more" fetches an older page, and the trace link opens the right trace in a new tab.

- [ ] **Step 5: Commit**

```bash
git add apps/annotation-studio/frontend/src/components/InteractionRow.tsx \
  apps/annotation-studio/frontend/src/pages/ProjectDetail.tsx apps/annotation-studio/frontend/src/index.css
git commit -m "annotation-studio: implement interaction list with expand and full conversation"
```

---

### Task 11: Label picker and annotation save

**Files:**
- Modify: `apps/annotation-studio/frontend/src/components/InteractionRow.tsx`
- Modify: `apps/annotation-studio/frontend/src/pages/ProjectDetail.tsx`

**Interfaces:**
- Consumes: `api.upsertAnnotation` (Task 7), `types.Label` (Task 7).

- [ ] **Step 1: Add the label picker, description textarea, save, and current-label badge to `InteractionRow.tsx`**

Add to the imports:

```tsx
import { upsertAnnotation } from "../api";
import type { Label } from "../types";
```

Change the `Props` interface and function signature:

```tsx
interface Props {
  projectId: number;
  interaction: Interaction;
  labels: Label[];
}

export function InteractionRow({ projectId, interaction, labels }: Props) {
```

Add state below the existing `expanded`/`showFullConversation` state:

```tsx
  const [labelId, setLabelId] = useState<number | null>(interaction.annotation?.label_id ?? null);
  const [description, setDescription] = useState(interaction.annotation?.description ?? "");
  const [savedLabelId, setSavedLabelId] = useState<number | null>(interaction.annotation?.label_id ?? null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const currentLabelName = labels.find((l) => l.id === savedLabelId)?.name ?? "Ungraded";

  const handleSaveAnnotation = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      await upsertAnnotation(projectId, interaction.trace_id, interaction.span_id, {
        label_id: labelId,
        description,
      });
      setSavedLabelId(labelId);
    } catch (err) {
      setSaveError(String(err));
    } finally {
      setSaving(false);
    }
  };
```

Add the current-label badge to the collapsed summary button (inside `.interaction-summary`, after `.preview`):

```tsx
        <span className="label-badge">{currentLabelName}</span>
```

Add the grading UI at the end of the expanded `.interaction-detail` block, right before the "Open trace in Logfire" link:

```tsx
          <h4>Grade</h4>
          <div className="label-picker">
            {labels.map((label) => (
              <button
                key={label.id}
                className={labelId === label.id ? "selected" : ""}
                onClick={() => setLabelId(label.id)}
              >
                {label.name}
              </button>
            ))}
          </div>
          <textarea
            rows={4}
            placeholder="Why this label?"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
          <button onClick={handleSaveAnnotation} disabled={saving}>
            {saving ? "Saving…" : "Save annotation"}
          </button>
          {saveError && <p className="error">{saveError}</p>}

```

- [ ] **Step 2: Pass `projectId` and `labels` from `ProjectDetail.tsx`**

```tsx
      {interactions.map((interaction) => (
        <InteractionRow
          key={`${interaction.trace_id}:${interaction.span_id}`}
          projectId={projectId}
          interaction={interaction}
          labels={project.labels}
        />
      ))}
```

- [ ] **Step 3: Add label-picker/badge styling** — append to `frontend/src/index.css`

```css
.label-badge {
  padding: 0.15rem 0.5rem;
  border-radius: 999px;
  background: #8882;
  font-size: 0.85em;
}

.label-picker {
  display: flex;
  gap: 0.5rem;
  margin: 0.5rem 0;
}

.label-picker button.selected {
  background: #4a90d9;
  color: white;
}
```

- [ ] **Step 4: Build and manually verify**

```bash
cd apps/annotation-studio/frontend
npm run build
```

With backend + `npm run dev` running, expand an interaction, pick a label, write a description, click "Save annotation," collapse and re-expand the row, and confirm the badge and picker reflect the saved state. Reload the page entirely and confirm it's still there (came back from SQLite via `GET /api/projects/{id}/interactions`).

- [ ] **Step 5: Commit**

```bash
git add apps/annotation-studio/frontend/src/components/InteractionRow.tsx \
  apps/annotation-studio/frontend/src/pages/ProjectDetail.tsx apps/annotation-studio/frontend/src/index.css
git commit -m "annotation-studio: implement label picker and annotation save"
```

---

### Task 12: Dockerfile, Compose service, and final integration check

**Files:**
- Create: `apps/annotation-studio/Dockerfile`
- Modify: `docker-compose.yml`
- Modify: `.gitignore`

**Interfaces:**
- None — this task wires existing pieces (Tasks 1–11) into the repo's Docker/Compose conventions and runs the full verification checklist.

- [ ] **Step 1: Create `apps/annotation-studio/Dockerfile`** (multi-stage: build the frontend, then copy its `dist/` into the Python image — the build context is the repo root, per `add-demo`'s convention, so `demo_core`'s path dependency resolves)

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

- [ ] **Step 2: Add the service to `docker-compose.yml`** (append at the end, before the top-level `volumes:` key — merge with the existing `volumes:` block if one already exists there)

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
```

Add to the top-level `volumes:` block:

```yaml
  annotation_studio_data:
```

- [ ] **Step 3: Ignore the local SQLite data directory** — append to `.gitignore`

```

# annotation-studio local SQLite data
apps/annotation-studio/data/
```

- [ ] **Step 4: Run the full verification checklist**

```bash
uv sync --all-packages
uv run pytest apps/annotation-studio/tests/ -v
cd apps/annotation-studio/frontend && npm run build && cd /Users/duncanmckinnon/Documents/code/pydantic-demos
cp apps/annotation-studio/.env.example apps/annotation-studio/.env
docker compose --profile annotation-studio config
```

Expected: all backend tests pass, frontend builds cleanly, and `docker compose config` resolves the `annotation-studio` service (and its `annotation_studio_data` volume) without error. `apps/annotation-studio/.env` now exists locally (gitignored) with empty credential values — fill in real `LOGFIRE_TOKEN` and `RX_ASSISTANT_LOGFIRE_READ_TOKEN` before actually running the container.

- [ ] **Step 5: Commit**

```bash
git add apps/annotation-studio/Dockerfile docker-compose.yml .gitignore
git commit -m "annotation-studio: add Dockerfile and Compose service"
```

---

## Self-Review

**Spec coverage:**
- One fixed source project, read-only, no auth → Tasks 1–2 (seeded project, no auth anywhere). ✓
- SQLite schema (projects/labels/annotations) → Task 2. ✓
- `AsyncLogfireQueryClient` read-token query, pagination, 14-day window → Task 4. ✓
- `top_level_agent_name` validation against SQL-injection → Task 3 (`validate_agent_name`), enforced in Task 4's query and Task 5's `PUT /api/projects/{id}`. ✓
- Input/output extraction rule, scrubbed-value pass-through, raw-attributes fallback → Task 3, corrected per real span data (see "Corrections to the Approved Spec"). ✓
- Trace link built from token's own `info()` + `base_url` → Task 4 (`build_trace_link`). ✓
- No write-back, no annotation-queue integration → never implemented anywhere in this plan; called out in Global Constraints. ✓
- Architecture file layout → matches Tasks 1–12's file list exactly. ✓
- API surface (`GET/PUT /api/projects`, `GET .../interactions`, `PUT .../annotations/...`) → Tasks 5–6. ✓
- Frontend pages (project list, project detail with criteria/labels/interactions/expand/full-conversation/label-picker/description/trace-link) → Tasks 7–11. ✓
- Frontend build deviation (multi-stage Dockerfile, two-process local dev) → Tasks 7, 12. ✓
- Settings (`SourceSettings`, `AppSettings`, `.env.example`) → Task 1. ✓
- Docker Compose service/volume/profile/port → Task 12. ✓
- Dependencies (`demo-core`, `logfire` explicit, `fastapi`, `uvicorn[standard]`, `python-dotenv`, no `pydantic-ai`/`pydantic-evals`/`jinja2`) → Task 1's `pyproject.toml`. ✓
- Testing conventions (conftest dummy env + temp-file DB path, no `tests/__init__.py`, monkeypatched Logfire query, `npm run build` as the frontend gate) → Task 1's conftest, Tasks 4/6's monkeypatching, Task 7's note. ✓

**Placeholder scan:** No TBD/TODO markers; every step has runnable code or an exact shell command; no "similar to Task N" references — repeated context (e.g., fixture-based tests, monkeypatching pattern) is written out in full each time.

**Type consistency:** `Interaction` dataclass fields (Task 3) match `asdict(interaction)` usage in Task 6's route and `types.ts`'s `Interaction` interface (Task 7) field-for-field, including the later-added `raw_attributes`. `fetch_project_interactions`'s signature (Task 4) matches every monkeypatch call in Task 6's tests. `ProjectUpdateRequest`/`AnnotationUpdateRequest` field names match `api.ts`'s `updateProject`/`upsertAnnotation` payload shapes and the `CriteriaEditor`/`LabelEditor`/`InteractionRow` components' `onSave` calls.

---

## Approved Revision Addendum (Authoritative)

This addendum preserves the original task-level code, fixtures, UI examples, rationale, and
verification context above while replacing the assumptions affected by design review. When
anything above conflicts with this addendum or the linked spec, this addendum and spec take
precedence. Execute these replacement tasks instead of original Tasks 1–12, reusing unchanged
fixtures and presentation code from the mapped original tasks.

## Revised Global Constraints

- No auth; annotator profiles are local identities.
- SQLite is authoritative; Logfire write-back is append-only.
- Use distinct `LOGFIRE_TOKEN`, `RX_ASSISTANT_LOGFIRE_READ_TOKEN`, and `RX_ASSISTANT_LOGFIRE_WRITE_TOKEN` values.
- Configure the writer with `logfire.configure(local=True, token=write_token, service_name="annotation-studio-writeback")`; never replace global app telemetry configuration.
- Validate agent names against `^[A-Za-z0-9_]+$` before storage and SQL interpolation.
- Tests never call real Logfire APIs and must not add `tests/__init__.py`.
- Project updates are atomic, label IDs remain stable, and annotation labels must belong to their project.
- Pagination is exclusive keyset pagination over `(start_timestamp, span_id)`.
- Frontend correctness gates are `npm run build` and manual browser verification.

---

### Replacement Task 1: Package and settings

**Files:** Create `apps/annotation-studio/pyproject.toml`, `.env.example`, `src/annotation_studio/__init__.py`, `src/annotation_studio/settings.py`, `tests/conftest.py`, and `tests/test_settings.py`.

**Interfaces:** Produces `SourceSettings(read_token, write_token, top_level_agent_name)` and `AppSettings(database_path)`.

- [ ] **Step 1: Write failing settings tests**

```python
def test_source_settings_reads_separate_tokens(monkeypatch):
    monkeypatch.setenv("RX_ASSISTANT_LOGFIRE_READ_TOKEN", "read")
    monkeypatch.setenv("RX_ASSISTANT_LOGFIRE_WRITE_TOKEN", "write")
    value = SourceSettings()
    assert (value.read_token, value.write_token) == ("read", "write")
    assert value.top_level_agent_name == "rx_assistant_agent"

def test_app_settings_default(monkeypatch):
    monkeypatch.delenv("ANNOTATION_STUDIO_DATABASE_PATH", raising=False)
    assert AppSettings().database_path == "data/annotation_studio.sqlite3"
```

Also parametrize a test deleting each source token and expecting `pydantic.ValidationError`.

- [ ] **Step 2: Run `uv run pytest apps/annotation-studio/tests/test_settings.py -v` and confirm import failure.**

- [ ] **Step 3: Implement settings**

```python
class SourceSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    read_token: str = Field(validation_alias="RX_ASSISTANT_LOGFIRE_READ_TOKEN")
    write_token: str = Field(validation_alias="RX_ASSISTANT_LOGFIRE_WRITE_TOKEN")
    top_level_agent_name: str = "rx_assistant_agent"

class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    database_path: str = Field(default="data/annotation_studio.sqlite3",
                               validation_alias="ANNOTATION_STUDIO_DATABASE_PATH")
```

Use the dependency/build configuration from the spec. Load the app `.env` in `__init__.py` with `override=False`. In `conftest.py`, force dummy values for all three tokens, force `LOGFIRE_SEND_TO_LOGFIRE=false`, set a temporary database path, and configure Logfire offline; never use `setdefault`.

- [ ] **Step 4: Run `uv sync --all-packages && uv run pytest apps/annotation-studio/tests/test_settings.py -v`.**

- [ ] **Step 5: Commit with `git commit -m "annotation-studio: scaffold settings and credentials"`.**

---

### Replacement Task 2: Transactional SQLite model

**Files:** Create `src/annotation_studio/db.py` and `tests/test_db.py`.

**Interfaces:** Produces schema/connection helpers, project and stable-label CRUD, annotator CRUD, annotator-scoped annotation upserts, and write-back status updates.

- [ ] **Step 1: Write failing database tests**

```python
def test_two_annotators_grade_same_interaction(conn):
    project, label = seeded_project_and_label(conn)
    ada = db.create_annotator(conn, "Ada")
    grace = db.create_annotator(conn, "Grace")
    a = db.upsert_annotation(conn, project["id"], "trace", "span", ada["id"], label["id"], "a")
    b = db.upsert_annotation(conn, project["id"], "trace", "span", grace["id"], label["id"], "b")
    assert a["id"] != b["id"]

def test_second_save_increments_revision_and_resets_writeback(conn):
    project, label, reviewer = seeded_entities(conn)
    first = db.upsert_annotation(conn, project["id"], "t", "s", reviewer["id"], label["id"], "one")
    db.mark_writeback_written(conn, first["id"], first["revision"])
    second = db.upsert_annotation(conn, project["id"], "t", "s", reviewer["id"], label["id"], "two")
    assert second["revision"] == 2
    assert second["writeback_status"] == "pending"

def test_label_rename_preserves_id_and_annotations(conn):
    project, label, reviewer = seeded_entities(conn)
    db.upsert_annotation(conn, project["id"], "t", "s", reviewer["id"], label["id"], "ok")
    labels = db.update_project(conn, project["id"], None, None, [db.LabelInput(label["id"], "Approved")])
    assert labels[0]["id"] == label["id"]

def test_combined_project_update_rolls_back_on_referenced_label_removal(conn):
    project, label, reviewer = seeded_entities(conn)
    db.upsert_annotation(conn, project["id"], "t", "s", reviewer["id"], label["id"], "ok")
    with pytest.raises(db.ConflictError):
        db.update_project(conn, project["id"], "changed", "changed_agent", [])
    assert db.get_project(conn, project["id"])["criteria_text"] == ""

def test_rejects_label_from_another_project(conn):
    project, _, reviewer = seeded_entities(conn)
    other_label = create_other_project_label(conn)
    with pytest.raises(db.ValidationError):
        db.upsert_annotation(conn, project["id"], "t", "s", reviewer["id"], other_label["id"], "bad")
```

Also test trimmed/non-empty/case-insensitively unique annotator names, profile rename, unused deletion, referenced-profile deletion conflict, idempotent seed, and stale revision status updates.

- [ ] **Step 2: Run `uv run pytest apps/annotation-studio/tests/test_db.py -v` and confirm failure.**

- [ ] **Step 3: Implement the spec schema plus these exact interfaces**

Create frozen `LabelInput(id: int | None, name: str)`, plus `ValidationError` and
`ConflictError`. Implement `init_db`, `seed_default_project`, `list_projects`, `get_project`,
`get_label`, `list_labels`, `update_project`, `list_annotators`, `get_annotator`,
`create_annotator`, `rename_annotator`, `delete_annotator`, `get_annotation`,
`upsert_annotation`, `mark_writeback_written`, and `mark_writeback_failed` with the exact
argument and return contracts stated in this task's Interfaces and exercised by Step 1's
tests.

`update_project` explicitly begins one transaction, validates stable IDs/names, applies every field, and rolls back on any exception. `upsert_annotation` validates annotator existence and label ownership, increments revision on conflict, and resets write-back fields. Status updates include both annotation ID and revision in their `WHERE` clause.

- [ ] **Step 4: Run the database tests and confirm they pass.**
- [ ] **Step 5: Commit with `git commit -m "annotation-studio: add transactional reviewer data model"`.**

---

### Replacement Task 3: Parsing and stable Logfire pagination

**Files:** Create `src/annotation_studio/logfire_client.py`, `tests/test_logfire_client.py`, and a trimmed real-span JSON fixture.

**Interfaces:** Produces `Interaction`, `Cursor`, `encode_cursor`, `decode_cursor`, `validate_agent_name`, `parse_interaction`, and `fetch_project_interactions`.

- [ ] **Step 1: Write failing tests for corrected first-new-user extraction, `final_result` preference, raw fallback, invalid agent names, cursor round-trip/validation, same-timestamp ordering, `limit + 1`, and no page-boundary duplication.**

```python
def test_cursor_round_trip():
    value = Cursor("2026-08-28T00:00:00Z", "c7a2373c3fe61d3f")
    assert decode_cursor(encode_cursor(value)) == value

async def test_fetches_extra_row_and_orders_stably(monkeypatch):
    fake = FakeQueryClient(three_rows())
    monkeypatch.setattr(module, "AsyncLogfireQueryClient", lambda _: fake)
    items, cursor = await fetch_project_interactions("read", "rx_assistant_agent", None, 2)
    assert len(items) == 2 and cursor is not None
    assert fake.limit == 3
    assert "ORDER BY start_timestamp DESC, span_id DESC" in fake.sql
```

- [ ] **Step 2: Run the focused test and confirm failure.**

- [ ] **Step 3: Implement types and query**

```python
@dataclass(frozen=True)
class Cursor:
    start_timestamp: str
    span_id: str

@dataclass
class Interaction:
    trace_id: str; span_id: str; start_timestamp: str
    input_text: str; output_text: str
    full_conversation: list[dict]; trace_url: str
    raw_attributes: dict | None = None
```

Encode cursor JSON with URL-safe base64 and validate decoded types, ISO timestamp, and 16-hex span ID. Validate agent names. On later pages add this SQL predicate after safely formatting the validated values:

```sql
AND (start_timestamp < timestamp '{cursor_timestamp}'
 OR (start_timestamp = timestamp '{cursor_timestamp}' AND span_id < '{cursor_span_id}'))
ORDER BY start_timestamp DESC, span_id DESC
```

Keep the 14-day bound, request `page_size + 1`, return only `page_size`, and return a cursor only if the extra row exists. Parsing follows the spec exactly.

- [ ] **Step 4: Run `uv run pytest apps/annotation-studio/tests/test_logfire_client.py -v`.**
- [ ] **Step 5: Commit with `git commit -m "annotation-studio: add parsing and keyset pagination"`.**

---

### Replacement Task 4: Append-only Logfire writer

**Files:** Create `src/annotation_studio/logfire_writer.py` and `tests/test_logfire_writer.py`.

**Interfaces:** Produces `build_event_key(annotation) -> str` and `AnnotationWriter.write(annotation, annotator, label) -> None`; write raises `WritebackError` when the forced flush fails.

- [ ] **Step 1: Write failing tests using a fake client**

```python
def test_uses_local_configuration(monkeypatch):
    calls = []
    monkeypatch.setattr(logfire, "configure", lambda **kw: calls.append(kw) or FakeLogfire())
    AnnotationWriter("write")
    assert calls == [{"local": True, "token": "write", "service_name": "annotation-studio-writeback"}]

def test_attaches_parent_and_tags_reviewer(fake_logfire):
    AnnotationWriter("write", client=fake_logfire).write(annotation(), annotator(), label())
    assert fake_logfire.context["traceparent"] == "00-01a045b8d6d40acd6c98ee00f1a3fe93-c7a2373c3fe61d3f-01"
    assert fake_logfire.events[0]["event_key"] == "annotation:11:revision:2"
    assert "annotator-7" in fake_logfire.events[0]["_tags"]

def test_false_flush_result_is_a_write_failure(fake_logfire):
    fake_logfire.flush_result = False
    with pytest.raises(WritebackError):
        AnnotationWriter("write", client=fake_logfire).write(annotation(), annotator(), label())
```

Also assert all spec attributes and invalid trace/span rejection.

- [ ] **Step 2: Run the test and confirm failure.**

- [ ] **Step 3: Implement**

```python
class AnnotationWriter:
    def __init__(self, write_token: str, client=None):
        self.client = client or logfire.configure(local=True, token=write_token,
                                                  service_name="annotation-studio-writeback")

    def write(self, annotation: dict, annotator: dict, label: dict | None) -> None:
        validate_trace_and_span(annotation["trace_id"], annotation["span_id"])
        with logfire.attach_context({"traceparent":
              f"00-{annotation['trace_id']}-{annotation['span_id']}-01"}):
            self.client.info("annotation_studio.annotation",
                _tags=["annotation-studio", "human-annotation", f"annotator-{annotator['id']}"],
                event_key=f"annotation:{annotation['id']}:revision:{annotation['revision']}",
                annotation_id=annotation["id"], annotation_revision=annotation["revision"],
                annotator_id=annotator["id"], annotator_name=annotator["name"],
                label_id=label["id"] if label else None, label_name=label["name"] if label else None,
                description=annotation["description"], project_id=annotation["project_id"],
                source_trace_id=annotation["trace_id"], source_span_id=annotation["span_id"])
        if not self.client.force_flush(timeout_millis=3000):
            raise WritebackError("Logfire exporter did not flush within 3000ms")
```

- [ ] **Step 4: Run writer tests.**
- [ ] **Step 5: Commit with `git commit -m "annotation-studio: append annotation revisions to traces"`.**

---

### Replacement Task 5: FastAPI APIs and write-back orchestration

**Files:** Create `src/annotation_studio/main.py`, `src/annotation_studio/routes.py`, and `tests/test_routes.py`.

**Interfaces:** Produces `create_annotation_studio_app(send_to_logfire=False, connection=None, writer=None)` and every API in the spec.

- [ ] **Step 1: Write failing tests for project GET/atomic PUT, agent-name rejection, annotator CRUD/duplicate/referenced deletion, required/valid interaction `annotator_id`, reviewer-specific merges, label ownership, successful write-back, and failure-after-local-save.**

```python
def test_failed_writeback_keeps_saved_grade(client_with_failing_writer):
    response = save_grade(client_with_failing_writer)
    assert response.status_code == 200
    assert response.json()["writeback_status"] == "failed"
    assert response.json()["description"] == "Grounded"

def test_other_reviewers_grade_is_not_merged(client, fake_fetch):
    ada, grace = create_reviewers(client)
    save_grade(client, ada["id"])
    page = client.get(f"/api/projects/1/interactions?annotator_id={grace['id']}").json()
    assert page["items"][0]["annotation"] is None
```

- [ ] **Step 2: Run route tests and confirm failure.**

- [ ] **Step 3: Implement request models and routes**

```python
class LabelPayload(BaseModel): id: int | None = None; name: str
class ProjectUpdateRequest(BaseModel):
    criteria_text: str | None = None
    top_level_agent_name: str | None = None
    labels: list[LabelPayload] | None = None
class AnnotatorRequest(BaseModel): name: str
class AnnotationUpdateRequest(BaseModel):
    annotator_id: int
    label_id: int | None = None
    description: str = ""
```

Map validation/conflict/missing resources to 400/409/404. Interaction listing requires a valid annotator, catches cursor `ValueError` as 400, calls the query wrapper, and merges `get_annotation(conn, project_id, interaction.trace_id, interaction.span_id, annotator_id)`.

Annotation save performs the local upsert, loads reviewer/label, calls the writer, then marks the same revision written. On writer exception, mark it failed with `ExceptionClass: message` truncated to 500 characters and return 200 with the saved grade. Never include credentials in error text.

`main.py` configures global app telemetry first, creates/seeds SQLite, constructs one local writer, registers routes, mounts `/assets`, and installs a final non-API SPA fallback. Dependency injection prevents writer construction in tests.

- [ ] **Step 4: Run `uv run pytest apps/annotation-studio/tests/ -v`.**
- [ ] **Step 5: Commit with `git commit -m "annotation-studio: add reviewer APIs and writeback"`.**

---

### Replacement Task 6: React foundation and annotator page

**Files:** Create `frontend/package.json`, TypeScript/Vite configs, `index.html`, `src/main.tsx`, `src/App.tsx`, `src/api.ts`, `src/types.ts`, `src/annotator.tsx`, `src/pages/Annotators.tsx`, `src/pages/ProjectList.tsx`, and `src/index.css`.

**Interfaces:** Produces typed API calls and `AnnotatorProvider`/`useAnnotator`.

- [ ] **Step 1: Configure React 18, Router 6, React Markdown 9, TypeScript 5.5, and Vite 5; use `tsc -b && vite build` and proxy `/api` to port 8000.**

- [ ] **Step 2: Define types matching API fields, including stable `LabelInput`, annotator, annotation revision, and `"pending" | "written" | "failed"` status. Implement API functions for annotator CRUD, projects, reviewer-scoped interactions, and annotation upsert. Encode path/query components.**

- [ ] **Step 3: Implement local selection context**

```tsx
const STORAGE_KEY = "annotation-studio.annotator-id";
// Initialize from localStorage, fetch profiles, clear an ID absent from the fetched list,
// and make setSelectedId update state and localStorage together.
```

The provider uses an effect to call `listAnnotators()`, a second effect to clear a selected ID absent from the returned list, and callbacks that update state and `localStorage` together. `/annotators` supports create, rename, select, and delete; show 409 errors beside the affected profile. The header shows the active name or “Choose annotator”.

- [ ] **Step 4: Run `npm install && npm run build`; manually verify selection survives reload and clears after selected-profile deletion.**
- [ ] **Step 5: Commit with `git commit -m "annotation-studio: add frontend annotator profiles"`.**

---

### Replacement Task 7: Project and grading UI

**Files:** Create `frontend/src/pages/ProjectDetail.tsx`, `components/ProjectEditor.tsx`, and `components/InteractionRow.tsx`; modify types and CSS.

- [ ] **Step 1: Implement one atomic editor form. Label state is `{id?: number, name: string}[]`; rename preserves IDs, reorder moves objects, and additions omit IDs. Show 400/409 errors without replacing loaded state.**

- [ ] **Step 2: Gate project detail on a selected annotator and reset/reload interactions when project or annotator changes**

```tsx
if (selectedId === null) return <Navigate to="/annotators" replace />;
const page = await listInteractions(projectId, selectedId, cursor);
setInteractions(old => cursor ? [...old, ...page.items] : page.items);
```

- [ ] **Step 3: Render markdown input/output, raw fallback, full tool transcript, trace link, label picker, description, badge, and save**

```tsx
const saved = await upsertAnnotation(projectId, interaction.trace_id, interaction.span_id, {
  annotator_id: selectedId, label_id: labelId, description,
});
setAnnotation(saved);
```

For failed write-back show “Grade saved locally, but Logfire write-back failed: …” while retaining saved state. For success show a subtle “Written to Logfire”. Sync component state when the interaction/annotator prop changes.

- [ ] **Step 4: Run `npm run build`; manually grade the same interaction differently as two reviewers, rename an in-use label, and verify a forced writer failure warning.**
- [ ] **Step 5: Commit with `git commit -m "annotation-studio: add reviewer grading workflow"`.**

---

### Replacement Task 8: Docker and final verification

**Files:** Create `apps/annotation-studio/Dockerfile`; modify `docker-compose.yml` and `.gitignore`.

- [ ] **Step 1: Add a Node 20 frontend-build stage and Python 3.11 runtime stage. Copy `frontend/dist` to `src/annotation_studio/static/dist`, run `uv sync --frozen --package annotation-studio`, and serve Uvicorn on port 8000.**

- [ ] **Step 2: Add `annotation-studio` Compose service on `8003:8000`, profiles `annotation-studio`/`all`, app `.env`, and named volume `annotation_studio_data:/app/apps/annotation-studio/data`. Ignore data, node_modules, and built dist.**

- [ ] **Step 3: Verify**

```bash
uv sync --all-packages
uv run pytest apps/annotation-studio/tests/ -v
cd apps/annotation-studio/frontend && npm run build
cd /Users/duncanmckinnon/Documents/code/pydantic-demos
docker compose --profile annotation-studio config
docker compose --profile annotation-studio build annotation-studio
```

- [ ] **Step 4: With real gitignored tokens, save revision 1 and edit to revision 2. Verify two `annotation_studio.annotation` children under the source agent span, stable event keys, reviewer tags, written SQLite statuses, and independent grades after switching reviewer.**

- [ ] **Step 5: Commit with `git commit -m "annotation-studio: add container integration"`.**

---


## Revision Coverage Map

- Replacement Task 1 supersedes original Task 1.
- Replacement Task 2 supersedes original Task 2.
- Replacement Task 3 supersedes original Tasks 3–4 and retains their real-span fixtures,
  raw fallback, trace-link construction, and query fakes.
- Replacement Task 4 is new: the separately configured append-only Logfire writer.
- Replacement Task 5 supersedes original Tasks 5–6 and retains the SPA static mount.
- Replacement Tasks 6–7 supersede original Tasks 7–11 while retaining Vite configuration,
  markdown/transcript rendering, styling, and explicit-save behavior.
- Replacement Task 8 supersedes original Task 12 and retains its multi-stage Docker pattern.
