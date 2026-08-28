# annotation-studio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `annotation-studio`, a new `apps/annotation-studio` FastAPI + React demo that lets one or more named reviewers browse `rx-assistant` agent interactions pulled live from Logfire, grade each one against a per-project criteria block, and store the label + written justification in local SQLite — with each grade also appended, best-effort, as a child log entry on the original Logfire trace.

**Architecture:** A FastAPI backend (`src/annotation_studio/`) exposes `/api/*` JSON routes backed by stdlib `sqlite3` (projects/labels/annotators/annotations) and `logfire.experimental.query_client.AsyncLogfireQueryClient` (read-only interaction data from `rx-assistant-demo`, with exclusive keyset pagination). A separate, token-scoped `logfire.configure(local=True, ...)` client appends each saved grade as a child log entry on the source trace — append-only, best-effort, never blocking or discarding the locally saved grade. A React + TypeScript + Vite SPA (`frontend/`) consumes the API, gated on a locally-selected named annotator profile, and is built to static files the backend serves.

**Tech Stack:** Python 3.11, FastAPI, stdlib `sqlite3`, `logfire` (query client + a second local write client, no agent), `demo_core`, `anyio`; React 18, TypeScript, Vite, React Router, `react-markdown`.

**Spec:** [docs/superpowers/specs/2026-08-28-annotation-studio-design.md](../specs/2026-08-28-annotation-studio-design.md)

## Global Constraints

- No auth; annotator profiles are local named identities, not real authentication.
- Only `rx-assistant-demo`'s trace shape is in scope for v1 — one fixed seeded project.
- **No integration with Logfire's native annotation queue** — gated feature, no public API.
  This app's SQLite remains the system of record; write-back (below) is a one-way export to
  the source trace, not a sync with that queue.
- SQLite is authoritative; Logfire write-back is append-only and best-effort — a failed
  write-back never blocks or discards a locally saved grade (see Task 5).
- Use distinct `LOGFIRE_TOKEN`, `RX_ASSISTANT_LOGFIRE_READ_TOKEN`, and `RX_ASSISTANT_LOGFIRE_WRITE_TOKEN` values.
- Configure the writer with `logfire.configure(local=True, token=write_token, service_name="annotation-studio-writeback")`; never replace global app telemetry configuration.
- Validate agent names against `^[A-Za-z0-9_]+$`, trace IDs against `^[0-9a-f]{32}$`, and span
  IDs against `^[0-9a-f]{16}$` before storage, SQL interpolation, or traceparent construction.
- When attaching write-back context to a remote trace, pass Logfire's own
  `TraceContextTextMapPropagator()` explicitly to `logfire.attach_context()` — its default
  global text-map propagator can be guard-wrapped depending on `distributed_tracing` config
  and silently no-op the extraction, making the write-back an orphan log instead of a child of
  the source span. (Verified against the installed `logfire` package: its own
  `logfire.experimental.annotations.raw_annotate_span` does exactly this for the same reason.)
- Mirror `logfire.experimental.annotations`' attribute conventions (`logfire.feedback.name`,
  `logfire.feedback.comment`) in the writer's own attributes so Logfire's UI recognizes these
  as feedback, not generic child spans. Its `record_feedback()` helper can't be called
  directly — it always writes through the global Logfire instance, and this app needs a
  separately-token-scoped `local=True` client — so the convention is replicated against the
  writer's own client instead of importing the helper.
- The writer's `force_flush()` blocks for up to 3 seconds; run it off the event loop with
  `anyio.to_thread.run_sync` in the route handler so one slow flush can't stall every other
  concurrent request in this single-process demo.
- Tests never call real Logfire APIs and must not add `tests/__init__.py`.
- Project updates (criteria, agent name, labels) are one atomic transaction; label IDs remain
  stable across renames/reorders; an annotation's label must belong to its own project.
- Pagination is exclusive keyset pagination over `(start_timestamp, span_id)`.
- Frontend correctness gates are `npm run build` and manual browser verification — no
  component-level test suite for v1 given the small surface area.

## Design Note: Input/Output Extraction Direction

`parse_interaction` (Task 3) determines a turn's input/output from `pydantic_ai.all_messages`
using `new_message_index` as a **lower bound** (`>= new_message_index`), not an upper one —
`new_message_index` marks where *this turn's new messages begin*, so the turn's own question
sits at `all_messages[new_message_index]`, not before it. This was verified against a real
`invoke_agent rx_assistant_agent` span pulled live from `rx-assistant-demo` during design
(trace `01a045b8d6d40acd6c98ee00f1a3fe93`): index 38 held `"What about major depressive
disorder?"`, the actual question that span answers. The fixture in Task 3 is that real span's
message shape, trimmed for a self-contained test.

---

### Task 1: Package and settings

**Files:**
- Create: `apps/annotation-studio/pyproject.toml`
- Create: `apps/annotation-studio/.env.example`
- Create: `apps/annotation-studio/src/annotation_studio/__init__.py`
- Create: `apps/annotation-studio/src/annotation_studio/settings.py`
- Test: `apps/annotation-studio/tests/conftest.py`
- Test: `apps/annotation-studio/tests/test_settings.py`

**Interfaces:**
- Produces: `annotation_studio.settings.SourceSettings` (fields: `read_token: str`,
  `write_token: str`, `top_level_agent_name: str = "rx_assistant_agent"`) and
  `annotation_studio.settings.AppSettings` (field: `database_path: str`) — every later task's
  settings usage.

- [ ] **Step 1: Create `pyproject.toml`**

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

No new dependency is needed for `anyio` (pulled in transitively by `fastapi`/`starlette`) or
`opentelemetry.trace` (pulled in transitively by `logfire`) — both are imported directly in
later tasks but already resolve through existing dependencies.

- [ ] **Step 2: Create `.env.example`**

```
# Self-instrumentation only (this app's own FastAPI/SQLite traces). Mint a fresh
# write token for a new dedicated 'annotation-studio' Logfire project — do not
# reuse rx-assistant's token here.
LOGFIRE_TOKEN=

# Read-only access to rx-assistant's Logfire project, minted separately from
# rx-assistant's own LOGFIRE_TOKEN (apps/rx-assistant/.env is not shared).
RX_ASSISTANT_LOGFIRE_READ_TOKEN=

# Append-only annotation events sent to the rx-assistant Logfire project. This is
# distinct from both the read token above and Annotation Studio's LOGFIRE_TOKEN.
RX_ASSISTANT_LOGFIRE_WRITE_TOKEN=

ANNOTATION_STUDIO_DATABASE_PATH=data/annotation_studio.sqlite3
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


def test_source_settings_reads_separate_tokens(monkeypatch) -> None:
    monkeypatch.setenv("RX_ASSISTANT_LOGFIRE_READ_TOKEN", "pylf_read_test")
    monkeypatch.setenv("RX_ASSISTANT_LOGFIRE_WRITE_TOKEN", "pylf_write_test")

    settings = SourceSettings()

    assert settings.read_token == "pylf_read_test"
    assert settings.write_token == "pylf_write_test"
    assert settings.top_level_agent_name == "rx_assistant_agent"


@pytest.mark.parametrize("missing_var", ["RX_ASSISTANT_LOGFIRE_READ_TOKEN", "RX_ASSISTANT_LOGFIRE_WRITE_TOKEN"])
def test_source_settings_requires_both_tokens(monkeypatch, missing_var: str) -> None:
    monkeypatch.setenv("RX_ASSISTANT_LOGFIRE_READ_TOKEN", "read")
    monkeypatch.setenv("RX_ASSISTANT_LOGFIRE_WRITE_TOKEN", "write")
    monkeypatch.delenv(missing_var, raising=False)

    with pytest.raises(ValidationError):
        SourceSettings()


def test_app_settings_default(monkeypatch) -> None:
    monkeypatch.delenv("ANNOTATION_STUDIO_DATABASE_PATH", raising=False)

    assert AppSettings().database_path == "data/annotation_studio.sqlite3"


def test_app_settings_reads_override(monkeypatch) -> None:
    monkeypatch.setenv("ANNOTATION_STUDIO_DATABASE_PATH", "/tmp/x.sqlite3")

    assert AppSettings().database_path == "/tmp/x.sqlite3"
```

- [ ] **Step 5: Create `tests/conftest.py`** (dummy env vars so settings can construct without
  a real `.env`; a temp-file database path so the module-level `app = create_annotation_studio_app()`
  built in Task 5 never touches a real file in the repo)

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
os.environ["RX_ASSISTANT_LOGFIRE_WRITE_TOKEN"] = "test-write-token"
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
    """Read spans and append annotation events in rx-assistant's Logfire project."""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    read_token: str = Field(validation_alias="RX_ASSISTANT_LOGFIRE_READ_TOKEN")
    write_token: str = Field(validation_alias="RX_ASSISTANT_LOGFIRE_WRITE_TOKEN")
    top_level_agent_name: str = Field(default="rx_assistant_agent")


class AppSettings(BaseSettings):
    """This app's own local settings — its SQLite database."""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    database_path: str = Field(
        default="data/annotation_studio.sqlite3", validation_alias="ANNOTATION_STUDIO_DATABASE_PATH"
    )
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
- Produces: `db.get_connection(database_path) -> sqlite3.Connection`, `db.init_db(conn)`,
  `db.seed_default_project(conn, top_level_agent_name)`, `db.list_projects(conn) -> list[dict]`,
  `db.get_project(conn, project_id) -> dict | None`, `db.list_labels(conn, project_id) -> list[dict]`,
  `db.get_label(conn, label_id) -> dict | None`, `db.LabelInput(id, name)`, `db.ValidationError`,
  `db.ConflictError`, `db.update_project(conn, project_id, criteria_text, top_level_agent_name, labels) -> dict`,
  `db.create_annotator`, `db.rename_annotator`, `db.delete_annotator`, `db.list_annotators`,
  `db.get_annotator`, `db.get_annotation(conn, project_id, trace_id, span_id, annotator_id) -> dict | None`,
  `db.upsert_annotation`, `db.mark_writeback_written`, `db.mark_writeback_failed`. Every
  returned dict has plain JSON-serializable values. Consumed directly by `main.py`/`routes.py`
  (Task 5) and, via `validate_agent_name`, imports from `logfire_client` (Task 3).

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


def _seeded_project(conn: sqlite3.Connection) -> dict:
    db.seed_default_project(conn, "rx_assistant_agent")
    return db.list_projects(conn)[0]


def _label_id(conn: sqlite3.Connection, project_id: int, name: str) -> int:
    return next(label["id"] for label in db.list_labels(conn, project_id) if label["name"] == name)


def test_seed_default_project_creates_project_and_starter_labels() -> None:
    conn = _fresh_conn()

    project = _seeded_project(conn)

    assert project["name"] == "rx-assistant"
    assert project["top_level_agent_name"] == "rx_assistant_agent"
    labels = db.list_labels(conn, project["id"])
    assert [label["name"] for label in labels] == ["Pass", "Neutral", "Fail"]


def test_seed_default_project_is_idempotent() -> None:
    conn = _fresh_conn()
    db.seed_default_project(conn, "rx_assistant_agent")

    db.seed_default_project(conn, "rx_assistant_agent")

    assert len(db.list_projects(conn)) == 1


def test_update_project_updates_criteria_only() -> None:
    conn = _fresh_conn()
    project = _seeded_project(conn)

    updated = db.update_project(conn, project["id"], "Be strict.", None, None)

    assert updated["criteria_text"] == "Be strict."
    assert updated["top_level_agent_name"] == "rx_assistant_agent"


def test_update_project_rejects_invalid_agent_name_and_changes_nothing() -> None:
    conn = _fresh_conn()
    project = _seeded_project(conn)

    with pytest.raises(ValueError):
        db.update_project(conn, project["id"], "changed", "not valid; DROP TABLE", None)

    assert db.get_project(conn, project["id"])["criteria_text"] == ""


def test_update_project_labels_rename_reorder_preserves_ids() -> None:
    conn = _fresh_conn()
    project = _seeded_project(conn)
    pass_id = _label_id(conn, project["id"], "Pass")

    updated = db.update_project(
        conn, project["id"], None, None,
        [db.LabelInput(None, "Fail"), db.LabelInput(pass_id, "Approved")],
    )

    assert [label["name"] for label in updated["labels"]] == ["Fail", "Approved"]
    assert next(l["id"] for l in updated["labels"] if l["name"] == "Approved") == pass_id


def test_update_project_rejects_label_id_from_another_project() -> None:
    conn = _fresh_conn()
    project = _seeded_project(conn)
    now = "2026-01-01T00:00:00"
    conn.execute(
        "INSERT INTO projects (name, top_level_agent_name, criteria_text, created_at, updated_at) "
        "VALUES ('other', 'other_agent', '', ?, ?)",
        (now, now),
    )
    conn.commit()
    other_project_id = conn.execute("SELECT id FROM projects WHERE name = 'other'").fetchone()["id"]
    conn.execute(
        "INSERT INTO labels (project_id, name, sort_order) VALUES (?, 'Foreign', 0)", (other_project_id,)
    )
    conn.commit()
    foreign_label_id = conn.execute("SELECT id FROM labels WHERE name = 'Foreign'").fetchone()["id"]

    with pytest.raises(ValueError):
        db.update_project(conn, project["id"], None, None, [db.LabelInput(foreign_label_id, "Hijacked")])


def test_update_project_combined_change_rolls_back_when_label_removal_conflicts() -> None:
    conn = _fresh_conn()
    project = _seeded_project(conn)
    annotator = db.create_annotator(conn, "Ada")
    neutral_id = _label_id(conn, project["id"], "Neutral")
    db.upsert_annotation(conn, project["id"], "trace-1", "span-1", annotator["id"], neutral_id, "why")

    with pytest.raises(db.ConflictError):
        db.update_project(
            conn, project["id"], "changed criteria", None,
            [db.LabelInput(_label_id(conn, project["id"], "Pass"), "Pass"),
             db.LabelInput(_label_id(conn, project["id"], "Fail"), "Fail")],
        )

    reloaded = db.get_project(conn, project["id"])
    assert reloaded["criteria_text"] == ""
    assert [l["name"] for l in db.list_labels(conn, project["id"])] == ["Pass", "Neutral", "Fail"]


def test_create_annotator_and_reject_case_insensitive_duplicate() -> None:
    conn = _fresh_conn()
    db.create_annotator(conn, "Ada")

    with pytest.raises(db.ConflictError):
        db.create_annotator(conn, "ada")


def test_rename_annotator_preserves_id() -> None:
    conn = _fresh_conn()
    ada = db.create_annotator(conn, "Ada")

    renamed = db.rename_annotator(conn, ada["id"], "Ada Lovelace")

    assert renamed["id"] == ada["id"]
    assert renamed["name"] == "Ada Lovelace"


def test_delete_unused_annotator_succeeds() -> None:
    conn = _fresh_conn()
    ada = db.create_annotator(conn, "Ada")

    db.delete_annotator(conn, ada["id"])

    assert db.list_annotators(conn) == []


def test_delete_referenced_annotator_raises_conflict() -> None:
    conn = _fresh_conn()
    project = _seeded_project(conn)
    ada = db.create_annotator(conn, "Ada")
    pass_id = _label_id(conn, project["id"], "Pass")
    db.upsert_annotation(conn, project["id"], "trace-1", "span-1", ada["id"], pass_id, "ok")

    with pytest.raises(db.ConflictError):
        db.delete_annotator(conn, ada["id"])


def test_two_annotators_grade_same_interaction_independently() -> None:
    conn = _fresh_conn()
    project = _seeded_project(conn)
    pass_id = _label_id(conn, project["id"], "Pass")
    ada = db.create_annotator(conn, "Ada")
    grace = db.create_annotator(conn, "Grace")

    a = db.upsert_annotation(conn, project["id"], "trace-1", "span-1", ada["id"], pass_id, "good")
    b = db.upsert_annotation(conn, project["id"], "trace-1", "span-1", grace["id"], pass_id, "also good")

    assert a["id"] != b["id"]
    assert db.get_annotation(conn, project["id"], "trace-1", "span-1", grace["id"])["id"] == b["id"]


def test_upsert_annotation_second_save_increments_revision_and_resets_writeback() -> None:
    conn = _fresh_conn()
    project = _seeded_project(conn)
    pass_id = _label_id(conn, project["id"], "Pass")
    ada = db.create_annotator(conn, "Ada")
    first = db.upsert_annotation(conn, project["id"], "trace-1", "span-1", ada["id"], pass_id, "one")
    db.mark_writeback_written(conn, first["id"], first["revision"])

    second = db.upsert_annotation(conn, project["id"], "trace-1", "span-1", ada["id"], pass_id, "two")

    assert second["revision"] == 2
    assert second["writeback_status"] == "pending"
    assert second["written_at"] is None


def test_upsert_annotation_rejects_unknown_annotator() -> None:
    conn = _fresh_conn()
    project = _seeded_project(conn)
    pass_id = _label_id(conn, project["id"], "Pass")

    with pytest.raises(ValueError):
        db.upsert_annotation(conn, project["id"], "trace-1", "span-1", 999, pass_id, "x")


def test_upsert_annotation_rejects_label_from_another_project() -> None:
    conn = _fresh_conn()
    project = _seeded_project(conn)
    ada = db.create_annotator(conn, "Ada")
    now = "2026-01-01T00:00:00"
    conn.execute(
        "INSERT INTO projects (name, top_level_agent_name, criteria_text, created_at, updated_at) "
        "VALUES ('other', 'other_agent', '', ?, ?)",
        (now, now),
    )
    conn.commit()
    other_project_id = conn.execute("SELECT id FROM projects WHERE name = 'other'").fetchone()["id"]
    conn.execute("INSERT INTO labels (project_id, name, sort_order) VALUES (?, 'Foreign', 0)", (other_project_id,))
    conn.commit()
    foreign_label_id = conn.execute("SELECT id FROM labels WHERE name = 'Foreign'").fetchone()["id"]

    with pytest.raises(ValueError):
        db.upsert_annotation(conn, project["id"], "trace-1", "span-1", ada["id"], foreign_label_id, "x")


def test_mark_writeback_written_sets_status_and_written_at() -> None:
    conn = _fresh_conn()
    project = _seeded_project(conn)
    pass_id = _label_id(conn, project["id"], "Pass")
    ada = db.create_annotator(conn, "Ada")
    annotation = db.upsert_annotation(conn, project["id"], "trace-1", "span-1", ada["id"], pass_id, "ok")

    db.mark_writeback_written(conn, annotation["id"], annotation["revision"])

    reloaded = db.get_annotation(conn, project["id"], "trace-1", "span-1", ada["id"])
    assert reloaded["writeback_status"] == "written"
    assert reloaded["written_at"] is not None


def test_mark_writeback_failed_sets_status_and_truncated_error() -> None:
    conn = _fresh_conn()
    project = _seeded_project(conn)
    pass_id = _label_id(conn, project["id"], "Pass")
    ada = db.create_annotator(conn, "Ada")
    annotation = db.upsert_annotation(conn, project["id"], "trace-1", "span-1", ada["id"], pass_id, "ok")

    db.mark_writeback_failed(conn, annotation["id"], annotation["revision"], "x" * 600)

    reloaded = db.get_annotation(conn, project["id"], "trace-1", "span-1", ada["id"])
    assert reloaded["writeback_status"] == "failed"
    assert len(reloaded["writeback_error"]) == 500


def test_mark_writeback_written_is_a_noop_for_a_stale_revision() -> None:
    conn = _fresh_conn()
    project = _seeded_project(conn)
    pass_id = _label_id(conn, project["id"], "Pass")
    ada = db.create_annotator(conn, "Ada")
    first = db.upsert_annotation(conn, project["id"], "trace-1", "span-1", ada["id"], pass_id, "one")
    db.upsert_annotation(conn, project["id"], "trace-1", "span-1", ada["id"], pass_id, "two")

    # A slow write-back for revision 1 completes after revision 2 was already saved locally —
    # it must not mark revision 2 "written".
    db.mark_writeback_written(conn, first["id"], first["revision"])

    reloaded = db.get_annotation(conn, project["id"], "trace-1", "span-1", ada["id"])
    assert reloaded["revision"] == 2
    assert reloaded["writeback_status"] == "pending"
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest apps/annotation-studio/tests/test_db.py -v
```

Expected: FAIL/ERROR — `annotation_studio.db` doesn't exist yet.

- [ ] **Step 3: Create `src/annotation_studio/db.py`**

```python
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from annotation_studio.logfire_client import validate_agent_name

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

CREATE TABLE IF NOT EXISTS annotators (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS annotations (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    trace_id TEXT NOT NULL,
    span_id TEXT NOT NULL,
    annotator_id INTEGER NOT NULL REFERENCES annotators(id),
    label_id INTEGER REFERENCES labels(id),
    description TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL DEFAULT 1,
    writeback_status TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'written' | 'failed'
    writeback_error TEXT,
    written_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, trace_id, span_id, annotator_id)
);
"""


class ValidationError(Exception):
    """A caller-supplied value is invalid — maps to HTTP 400 in routes.py."""


class ConflictError(Exception):
    """The requested change conflicts with existing data — maps to HTTP 409 in routes.py."""


@dataclass(frozen=True)
class LabelInput:
    id: int | None
    name: str


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


def list_labels(conn: sqlite3.Connection, project_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM labels WHERE project_id = ? ORDER BY sort_order", (project_id,)
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_label(conn: sqlite3.Connection, label_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM labels WHERE id = ?", (label_id,)).fetchone()
    return _row_to_dict(row) if row else None


def update_project(
    conn: sqlite3.Connection,
    project_id: int,
    criteria_text: str | None,
    top_level_agent_name: str | None,
    labels: list[LabelInput] | None,
) -> dict:
    """Apply every provided field in one transaction. A label id absent from this project, an
    invalid agent name, or removing a label still referenced by an annotation rolls back the
    *entire* call — including fields that would otherwise have succeeded — so the frontend's
    single "Save" button is all-or-nothing."""
    try:
        if top_level_agent_name is not None:
            validate_agent_name(top_level_agent_name)
            conn.execute(
                "UPDATE projects SET top_level_agent_name = ?, updated_at = ? WHERE id = ?",
                (top_level_agent_name, _now(), project_id),
            )

        if criteria_text is not None:
            conn.execute(
                "UPDATE projects SET criteria_text = ?, updated_at = ? WHERE id = ?",
                (criteria_text, _now(), project_id),
            )

        if labels is not None:
            existing_ids = {
                row["id"]
                for row in conn.execute(
                    "SELECT id FROM labels WHERE project_id = ?", (project_id,)
                ).fetchall()
            }
            keep_ids: set[int] = set()
            for order, label in enumerate(labels):
                if label.id is not None:
                    if label.id not in existing_ids:
                        raise ValidationError(f"Label {label.id} does not belong to this project")
                    conn.execute(
                        "UPDATE labels SET name = ?, sort_order = ? WHERE id = ?",
                        (label.name, order, label.id),
                    )
                    keep_ids.add(label.id)
                else:
                    cursor = conn.execute(
                        "INSERT INTO labels (project_id, name, sort_order) VALUES (?, ?, ?)",
                        (project_id, label.name, order),
                    )
                    keep_ids.add(cursor.lastrowid)

            for removed_id in existing_ids - keep_ids:
                conn.execute("DELETE FROM labels WHERE id = ?", (removed_id,))
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise ConflictError("Cannot remove a label used by an existing annotation") from exc
    except ValidationError:
        conn.rollback()
        raise

    conn.commit()
    project = get_project(conn, project_id)
    project["labels"] = list_labels(conn, project_id)
    return project


def create_annotator(conn: sqlite3.Connection, name: str) -> dict:
    name = name.strip()
    if not name:
        raise ValidationError("Annotator name cannot be empty")
    now = _now()
    try:
        cursor = conn.execute(
            "INSERT INTO annotators (name, created_at, updated_at) VALUES (?, ?, ?)",
            (name, now, now),
        )
    except sqlite3.IntegrityError:
        raise ConflictError(f"Annotator name {name!r} is already taken")
    conn.commit()
    return get_annotator(conn, cursor.lastrowid)


def rename_annotator(conn: sqlite3.Connection, annotator_id: int, name: str) -> dict:
    name = name.strip()
    if not name:
        raise ValidationError("Annotator name cannot be empty")
    try:
        conn.execute(
            "UPDATE annotators SET name = ?, updated_at = ? WHERE id = ?",
            (name, _now(), annotator_id),
        )
    except sqlite3.IntegrityError:
        raise ConflictError(f"Annotator name {name!r} is already taken")
    conn.commit()
    return get_annotator(conn, annotator_id)


def delete_annotator(conn: sqlite3.Connection, annotator_id: int) -> None:
    try:
        conn.execute("DELETE FROM annotators WHERE id = ?", (annotator_id,))
    except sqlite3.IntegrityError:
        conn.rollback()
        raise ConflictError("Cannot remove an annotator with existing annotations")
    conn.commit()


def list_annotators(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM annotators ORDER BY name COLLATE NOCASE").fetchall()
    return [_row_to_dict(row) for row in rows]


def get_annotator(conn: sqlite3.Connection, annotator_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM annotators WHERE id = ?", (annotator_id,)).fetchone()
    return _row_to_dict(row) if row else None


def get_annotation(
    conn: sqlite3.Connection, project_id: int, trace_id: str, span_id: str, annotator_id: int
) -> dict | None:
    row = conn.execute(
        "SELECT * FROM annotations WHERE project_id = ? AND trace_id = ? AND span_id = ? "
        "AND annotator_id = ?",
        (project_id, trace_id, span_id, annotator_id),
    ).fetchone()
    return _row_to_dict(row) if row else None


def upsert_annotation(
    conn: sqlite3.Connection,
    project_id: int,
    trace_id: str,
    span_id: str,
    annotator_id: int,
    label_id: int | None,
    description: str,
) -> dict:
    if get_annotator(conn, annotator_id) is None:
        raise ValidationError(f"Unknown annotator {annotator_id}")
    if label_id is not None and conn.execute(
        "SELECT 1 FROM labels WHERE id = ? AND project_id = ?", (label_id, project_id)
    ).fetchone() is None:
        raise ValidationError(f"Label {label_id} does not belong to this project")

    now = _now()
    existing = get_annotation(conn, project_id, trace_id, span_id, annotator_id)
    if existing:
        # Resets writeback_status/writeback_error/written_at — a new revision hasn't been
        # written yet, so a stale 'written'/written_at from the previous revision would be
        # misleading if the write-back for this one fails or is still in flight.
        conn.execute(
            "UPDATE annotations SET label_id = ?, description = ?, revision = revision + 1, "
            "writeback_status = 'pending', writeback_error = NULL, written_at = NULL, "
            "updated_at = ? WHERE id = ?",
            (label_id, description, now, existing["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO annotations (project_id, trace_id, span_id, annotator_id, label_id, "
            "description, revision, writeback_status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, 'pending', ?, ?)",
            (project_id, trace_id, span_id, annotator_id, label_id, description, now, now),
        )
    conn.commit()
    return get_annotation(conn, project_id, trace_id, span_id, annotator_id)


def mark_writeback_written(conn: sqlite3.Connection, annotation_id: int, revision: int) -> None:
    # revision in the WHERE clause: if the grade was re-saved (revision bumped) while a
    # slow write-back for the *previous* revision was still in flight, that stale call must
    # not mark the newer revision "written" — it simply matches no row and no-ops.
    now = _now()
    conn.execute(
        "UPDATE annotations SET writeback_status = 'written', writeback_error = NULL, "
        "written_at = ?, updated_at = ? WHERE id = ? AND revision = ?",
        (now, now, annotation_id, revision),
    )
    conn.commit()


def mark_writeback_failed(
    conn: sqlite3.Connection, annotation_id: int, revision: int, error: str
) -> None:
    conn.execute(
        "UPDATE annotations SET writeback_status = 'failed', writeback_error = ?, "
        "updated_at = ? WHERE id = ? AND revision = ?",
        (error[:500], _now(), annotation_id, revision),
    )
    conn.commit()
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest apps/annotation-studio/tests/test_db.py -v
```

Expected: 18 passed.

- [ ] **Step 5: Commit**

```bash
git add apps/annotation-studio/src/annotation_studio/db.py apps/annotation-studio/tests/test_db.py
git commit -m "annotation-studio: add SQLite schema and CRUD layer"
```

---

### Task 3: Message parsing and keyset Logfire pagination

Grounded in a real `invoke_agent rx_assistant_agent` span pulled from `rx-assistant-demo`
(trace `01a045b8d6d40acd6c98ee00f1a3fe93`) during design — see the Design Note above.

**Files:**
- Create: `apps/annotation-studio/src/annotation_studio/logfire_client.py`
- Create: `apps/annotation-studio/tests/fixtures/real_span_trimmed.json`
- Create: `apps/annotation-studio/tests/fixtures/final_result_present.json`
- Create: `apps/annotation-studio/tests/fixtures/malformed_attributes.json`
- Test: `apps/annotation-studio/tests/test_logfire_client.py`

**Interfaces:**
- Produces: `AGENT_NAME_PATTERN`, `validate_agent_name(name: str) -> None` (raises `ValueError`),
  `validate_trace_and_span(trace_id: str, span_id: str) -> None` (raises `ValueError`; reused by
  Task 4's writer), `Cursor(start_timestamp: str, span_id: str)` frozen dataclass,
  `encode_cursor(cursor: Cursor) -> str`, `decode_cursor(value: str) -> Cursor` (raises
  `ValueError`), `Interaction` dataclass (`trace_id, span_id, start_timestamp, input_text,
  output_text, full_conversation: list[dict], trace_url, raw_attributes: dict | None = None`),
  `parse_interaction(row: dict, trace_url: str) -> Interaction`, `build_trace_link(base_url,
  organization_name, project_name, trace_id) -> str`, `async fetch_project_interactions(
  read_token, top_level_agent_name, cursor: str | None, limit: int) -> tuple[list[Interaction],
  str | None]`. Consumed by `db.py` (Task 2, `validate_agent_name`), `logfire_writer.py`
  (Task 4, `validate_trace_and_span`), and `routes.py` (Task 5).

- [ ] **Step 1: Create the fixture files**

`apps/annotation-studio/tests/fixtures/real_span_trimmed.json` (real message shape, trimmed to
one prior turn + the turn this span answers):

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

`apps/annotation-studio/tests/fixtures/final_result_present.json` (tests that a present,
non-scrubbed `final_result` wins over the assistant message text):

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

`apps/annotation-studio/tests/fixtures/malformed_attributes.json` (tests the raw-attributes
fallback):

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


def _row(trace_id: str, start_timestamp: str, span_id: str = "span-1") -> dict:
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
    rows = [_row(f"trace-{i}", f"2026-08-28T00:0{i}:00Z", span_id=f"span-{i}") for i in range(3)]
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
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
uv run pytest apps/annotation-studio/tests/test_logfire_client.py -v
```

Expected: FAIL/ERROR — `annotation_studio.logfire_client` doesn't exist yet.

- [ ] **Step 4: Create `src/annotation_studio/logfire_client.py`**

```python
import base64
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from logfire.experimental.query_client import AsyncLogfireQueryClient

AGENT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")
TRACE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
SPAN_ID_PATTERN = re.compile(r"^[0-9a-f]{16}$")


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


def build_trace_link(base_url: str, organization_name: str, project_name: str, trace_id: str) -> str:
    return f"{base_url}/{organization_name}/{project_name}?q=trace_id='{trace_id}'"


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
    min_timestamp = datetime.now(timezone.utc) - timedelta(days=14)
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
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run pytest apps/annotation-studio/tests/test_logfire_client.py -v
```

Expected: 14 passed.

- [ ] **Step 6: Commit**

```bash
git add apps/annotation-studio/src/annotation_studio/logfire_client.py \
  apps/annotation-studio/tests/fixtures/ apps/annotation-studio/tests/test_logfire_client.py
git commit -m "annotation-studio: add message parsing and keyset Logfire pagination"
```

---

### Task 4: Append-only Logfire writer

**Files:**
- Create: `apps/annotation-studio/src/annotation_studio/logfire_writer.py`
- Test: `apps/annotation-studio/tests/test_logfire_writer.py`

**Interfaces:**
- Consumes: `validate_trace_and_span` (Task 3). Produces `build_event_key(annotation) -> str`
  and `AnnotationWriter.write(annotation, annotator, label) -> None`; write raises
  `WritebackError` when the forced flush fails or the trace/span ids fail validation. Consumed
  by `routes.py` (Task 5).

- [ ] **Step 1: Write failing tests using a fake client** (imports: `logfire`, `pytest`,
`from contextlib import contextmanager`, `from opentelemetry.trace.propagation.tracecontext
import TraceContextTextMapPropagator`)

```python
from contextlib import contextmanager

import logfire
import pytest
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from annotation_studio.logfire_writer import AnnotationWriter, WritebackError


class FakeLogfire:
    def __init__(self):
        self.context = {}
        self.events = []
        self.flush_result = True

    def info(self, message, **attrs):
        self.context = dict(attrs)  # simplification: test reads attach_context's carrier separately
        self.events.append(attrs)

    def force_flush(self, timeout_millis=3000):
        return self.flush_result


def annotation():
    return {"id": 11, "revision": 2, "trace_id": "01a045b8d6d40acd6c98ee00f1a3fe93",
            "span_id": "c7a2373c3fe61d3f", "project_id": 1, "description": "Grounded"}


def annotator():
    return {"id": 7, "name": "Ada"}


def label():
    return {"id": 3, "name": "Pass"}


@pytest.fixture
def fake_logfire():
    return FakeLogfire()


def test_uses_local_configuration(monkeypatch):
    calls = []
    monkeypatch.setattr(logfire, "configure", lambda **kw: calls.append(kw) or FakeLogfire())
    AnnotationWriter("write")
    assert calls == [{"local": True, "token": "write", "service_name": "annotation-studio-writeback"}]


def test_attaches_parent_and_tags_reviewer(fake_logfire):
    AnnotationWriter("write", client=fake_logfire).write(annotation(), annotator(), label())
    assert fake_logfire.events[0]["event_key"] == "annotation:11:revision:2"
    assert "annotator-7" in fake_logfire.events[0]["_tags"]


def test_uses_explicit_traceparent_propagator(monkeypatch, fake_logfire):
    # Regression test: logfire.attach_context()'s *default* propagator can be guard-wrapped
    # depending on distributed_tracing config and silently no-op the extraction, which would
    # make the write-back an orphan log instead of a child of the source span. The writer
    # must pass Logfire's own TraceContextTextMapPropagator() explicitly — the same thing
    # logfire.experimental.annotations.raw_annotate_span does for the same reason.
    calls = []
    real_attach_context = logfire.attach_context

    @contextmanager
    def spy(carrier, **kwargs):
        calls.append((carrier, kwargs.get("propagator")))
        with real_attach_context(carrier, **kwargs):
            yield

    monkeypatch.setattr(logfire, "attach_context", spy)
    AnnotationWriter("write", client=fake_logfire).write(annotation(), annotator(), label())
    carrier, propagator = calls[0]
    assert carrier["traceparent"] == "00-01a045b8d6d40acd6c98ee00f1a3fe93-c7a2373c3fe61d3f-01"
    assert isinstance(propagator, TraceContextTextMapPropagator)


def test_uses_logfire_feedback_attribute_conventions(fake_logfire):
    AnnotationWriter("write", client=fake_logfire).write(annotation(), annotator(), label())
    attrs = fake_logfire.events[0]
    assert attrs["logfire.feedback.name"] == "label"
    assert attrs["logfire.feedback.comment"] == annotation()["description"]


def test_rejects_invalid_trace_or_span_id(fake_logfire):
    with pytest.raises(WritebackError):
        AnnotationWriter("write", client=fake_logfire).write(
            {**annotation(), "trace_id": "not-hex"}, annotator(), label()
        )


def test_false_flush_result_is_a_write_failure(fake_logfire):
    fake_logfire.flush_result = False
    with pytest.raises(WritebackError):
        AnnotationWriter("write", client=fake_logfire).write(annotation(), annotator(), label())
```

- [ ] **Step 2: Run the test and confirm failure.**

```bash
uv run pytest apps/annotation-studio/tests/test_logfire_writer.py -v
```

- [ ] **Step 3: Create `src/annotation_studio/logfire_writer.py`**

```python
import logfire
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from annotation_studio.logfire_client import validate_trace_and_span

# Logfire's own logfire.experimental.annotations module uses this exact propagator for the
# same "attach to a span given its trace/span id" case — reused here rather than relying on
# attach_context()'s default global text-map propagator (see the regression test above).
TRACEPARENT_PROPAGATOR = TraceContextTextMapPropagator()


class WritebackError(Exception):
    pass


def build_event_key(annotation: dict) -> str:
    return f"annotation:{annotation['id']}:revision:{annotation['revision']}"


class AnnotationWriter:
    def __init__(self, write_token: str, client=None):
        self.client = client or logfire.configure(
            local=True, token=write_token, service_name="annotation-studio-writeback"
        )

    def write(self, annotation: dict, annotator: dict, label: dict | None) -> None:
        try:
            validate_trace_and_span(annotation["trace_id"], annotation["span_id"])
        except ValueError as exc:
            raise WritebackError(str(exc)) from exc

        traceparent = f"00-{annotation['trace_id']}-{annotation['span_id']}-01"
        label_name = label["name"] if label else None

        with logfire.attach_context({"traceparent": traceparent}, propagator=TRACEPARENT_PROPAGATOR):
            self.client.info(
                "annotation_studio.annotation",
                _tags=["annotation-studio", "human-annotation", f"annotator-{annotator['id']}"],
                event_key=build_event_key(annotation),
                annotation_id=annotation["id"],
                annotation_revision=annotation["revision"],
                annotator_id=annotator["id"],
                annotator_name=annotator["name"],
                label_id=label["id"] if label else None,
                label_name=label_name,
                description=annotation["description"],
                project_id=annotation["project_id"],
                source_trace_id=annotation["trace_id"],
                source_span_id=annotation["span_id"],
                # Attribute names Logfire's own logfire.experimental.annotations.
                # record_feedback() uses, so this renders as feedback in the Logfire UI
                # rather than a plain child log entry. record_feedback() itself can't be
                # called directly — it always writes through the *global* Logfire instance,
                # and this app needs a separately-token-scoped local=True client to keep
                # write-back out of annotation-studio's own Logfire project — so the
                # convention is replicated here against self.client instead.
                **{"logfire.feedback.name": "label", "logfire.feedback.comment": annotation["description"]},
            )
        if not self.client.force_flush(timeout_millis=3000):
            raise WritebackError("Logfire exporter did not flush within 3000ms")
```

- [ ] **Step 4: Run writer tests.**

```bash
uv run pytest apps/annotation-studio/tests/test_logfire_writer.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add apps/annotation-studio/src/annotation_studio/logfire_writer.py apps/annotation-studio/tests/test_logfire_writer.py
git commit -m "annotation-studio: append annotation revisions to traces"
```

---

### Task 5: FastAPI APIs and write-back orchestration

**Files:**
- Create: `apps/annotation-studio/src/annotation_studio/main.py`
- Create: `apps/annotation-studio/src/annotation_studio/routes.py`
- Test: `apps/annotation-studio/tests/test_routes.py`

**Interfaces:**
- Consumes: `db.*` (Task 2), `logfire_client.fetch_project_interactions`/`validate_agent_name`
  (Task 3), `logfire_writer.AnnotationWriter` (Task 4), `settings.AppSettings`/`SourceSettings`
  (Task 1).
- Produces: `main.create_annotation_studio_app(send_to_logfire=None, connection=None,
  writer=None) -> FastAPI` and module-level `main.app`; every route in the spec's API section.

- [ ] **Step 1: Write the failing tests** — `apps/annotation-studio/tests/test_routes.py`

```python
import sqlite3

from fastapi.testclient import TestClient

import annotation_studio.main as annotation_studio_main
import annotation_studio.routes as routes
from annotation_studio.logfire_client import Interaction


class FakeWriter:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = []

    def write(self, annotation, annotator, label):
        self.calls.append((annotation, annotator, label))
        if self.fail:
            raise RuntimeError("simulated Logfire outage")


def _app(writer=None) -> TestClient:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    app = annotation_studio_main.create_annotation_studio_app(
        send_to_logfire=False, connection=conn, writer=writer or FakeWriter()
    )
    return TestClient(app)


def _project_id(client: TestClient) -> int:
    return client.get("/api/projects").json()[0]["id"]


def _create_annotator(client: TestClient, name: str) -> dict:
    return client.post("/api/annotators", json={"name": name}).json()


def _pass_label_id(client: TestClient, project_id: int) -> int:
    return next(l["id"] for l in client.get(f"/api/projects/{project_id}").json()["labels"] if l["name"] == "Pass")


def test_list_projects_returns_seeded_project() -> None:
    client = _app()

    response = client.get("/api/projects")

    assert response.status_code == 200
    assert response.json()[0]["name"] == "rx-assistant"


def test_get_project_includes_labels() -> None:
    client = _app()
    project_id = _project_id(client)

    response = client.get(f"/api/projects/{project_id}")

    assert [l["name"] for l in response.json()["labels"]] == ["Pass", "Neutral", "Fail"]


def test_get_project_returns_404_for_unknown_id() -> None:
    client = _app()

    assert client.get("/api/projects/999").status_code == 404


def test_put_project_updates_criteria_agent_name_and_labels_atomically() -> None:
    client = _app()
    project_id = _project_id(client)
    pass_id = _pass_label_id(client, project_id)

    response = client.put(
        f"/api/projects/{project_id}",
        json={
            "criteria_text": "Be strict.",
            "top_level_agent_name": "rx_assistant_agent_v2",
            "labels": [{"id": pass_id, "name": "Approved"}, {"id": None, "name": "New"}],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["criteria_text"] == "Be strict."
    assert body["top_level_agent_name"] == "rx_assistant_agent_v2"
    assert [l["name"] for l in body["labels"]] == ["Approved", "New"]


def test_put_project_rejects_invalid_agent_name() -> None:
    client = _app()
    project_id = _project_id(client)

    response = client.put(f"/api/projects/{project_id}", json={"top_level_agent_name": "not valid; DROP TABLE"})

    assert response.status_code == 400


def test_put_project_returns_409_when_removing_label_in_use() -> None:
    client = _app()
    project_id = _project_id(client)
    annotator = _create_annotator(client, "Ada")
    neutral_id = next(l["id"] for l in client.get(f"/api/projects/{project_id}").json()["labels"] if l["name"] == "Neutral")
    client.put(
        f"/api/projects/{project_id}/annotations/trace-1/span-1",
        json={"annotator_id": annotator["id"], "label_id": neutral_id, "description": "why"},
    )

    response = client.put(f"/api/projects/{project_id}", json={"labels": [{"id": None, "name": "Only"}]})

    assert response.status_code == 409


def test_annotator_crud_lifecycle() -> None:
    client = _app()

    created = client.post("/api/annotators", json={"name": "Ada"})
    assert created.status_code == 200
    annotator_id = created.json()["id"]

    duplicate = client.post("/api/annotators", json={"name": "ada"})
    assert duplicate.status_code == 409

    renamed = client.put(f"/api/annotators/{annotator_id}", json={"name": "Ada Lovelace"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Ada Lovelace"

    assert client.get("/api/annotators").json()[0]["name"] == "Ada Lovelace"

    deleted = client.delete(f"/api/annotators/{annotator_id}")
    assert deleted.status_code == 204
    assert client.get("/api/annotators").json() == []


def test_delete_referenced_annotator_returns_409() -> None:
    client = _app()
    project_id = _project_id(client)
    annotator = _create_annotator(client, "Ada")
    pass_id = _pass_label_id(client, project_id)
    client.put(
        f"/api/projects/{project_id}/annotations/trace-1/span-1",
        json={"annotator_id": annotator["id"], "label_id": pass_id, "description": "ok"},
    )

    response = client.delete(f"/api/annotators/{annotator['id']}")

    assert response.status_code == 409


def test_list_interactions_requires_annotator_id() -> None:
    client = _app()
    project_id = _project_id(client)

    assert client.get(f"/api/projects/{project_id}/interactions").status_code == 400


def test_list_interactions_merges_only_the_requesting_annotators_grade(monkeypatch) -> None:
    client = _app()
    project_id = _project_id(client)
    ada = _create_annotator(client, "Ada")
    grace = _create_annotator(client, "Grace")
    pass_id = _pass_label_id(client, project_id)
    client.put(
        f"/api/projects/{project_id}/annotations/trace-1/span-1",
        json={"annotator_id": ada["id"], "label_id": pass_id, "description": "good"},
    )

    async def fake_fetch(read_token, top_level_agent_name, cursor, limit):
        return [
            Interaction(
                trace_id="trace-1", span_id="span-1", start_timestamp="2026-08-28T00:00:00Z",
                input_text="q", output_text="a", full_conversation=[],
                trace_url="https://logfire-us.pydantic.dev/duncan/rx-assistant-demo?q=trace_id='trace-1'",
            )
        ], None

    monkeypatch.setattr(routes, "fetch_project_interactions", fake_fetch)

    ada_page = client.get(f"/api/projects/{project_id}/interactions?annotator_id={ada['id']}").json()
    grace_page = client.get(f"/api/projects/{project_id}/interactions?annotator_id={grace['id']}").json()

    assert ada_page["items"][0]["annotation"]["label_id"] == pass_id
    assert grace_page["items"][0]["annotation"] is None


def test_upsert_annotation_creates_and_writes_back() -> None:
    writer = FakeWriter()
    client = _app(writer=writer)
    project_id = _project_id(client)
    annotator = _create_annotator(client, "Ada")
    pass_id = _pass_label_id(client, project_id)

    response = client.put(
        f"/api/projects/{project_id}/annotations/trace-1/span-1",
        json={"annotator_id": annotator["id"], "label_id": pass_id, "description": "Correct and grounded."},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["writeback_status"] == "written"
    assert body["written_at"] is not None
    assert len(writer.calls) == 1


def test_failed_writeback_keeps_saved_grade() -> None:
    client = _app(writer=FakeWriter(fail=True))
    project_id = _project_id(client)
    annotator = _create_annotator(client, "Ada")
    pass_id = _pass_label_id(client, project_id)

    response = client.put(
        f"/api/projects/{project_id}/annotations/trace-1/span-1",
        json={"annotator_id": annotator["id"], "label_id": pass_id, "description": "Grounded"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["writeback_status"] == "failed"
    assert body["description"] == "Grounded"
    assert "RuntimeError" in body["writeback_error"]


def test_upsert_annotation_rejects_unknown_annotator() -> None:
    client = _app()
    project_id = _project_id(client)

    response = client.put(
        f"/api/projects/{project_id}/annotations/trace-1/span-1",
        json={"annotator_id": 999, "label_id": None, "description": ""},
    )

    assert response.status_code == 400
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest apps/annotation-studio/tests/test_routes.py -v
```

Expected: FAIL/ERROR — `annotation_studio.main`/`annotation_studio.routes` don't exist yet.

- [ ] **Step 3: Create `src/annotation_studio/routes.py`**

```python
import sqlite3
from dataclasses import asdict

from anyio import to_thread
from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

from annotation_studio import db
from annotation_studio.logfire_client import fetch_project_interactions, validate_agent_name
from annotation_studio.logfire_writer import AnnotationWriter
from annotation_studio.settings import AppSettings, SourceSettings

PAGE_SIZE = 20


class LabelPayload(BaseModel):
    id: int | None = None
    name: str


class ProjectUpdateRequest(BaseModel):
    criteria_text: str | None = None
    top_level_agent_name: str | None = None
    labels: list[LabelPayload] | None = None


class AnnotatorRequest(BaseModel):
    name: str


class AnnotationUpdateRequest(BaseModel):
    annotator_id: int
    label_id: int | None = None
    description: str = ""


def register_routes(
    app: FastAPI,
    conn: sqlite3.Connection,
    source_settings: SourceSettings,
    app_settings: AppSettings,
    writer: AnnotationWriter,
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
        if db.get_project(conn, project_id) is None:
            raise HTTPException(status_code=404, detail="project_not_found")
        labels = (
            [db.LabelInput(id=label.id, name=label.name) for label in payload.labels]
            if payload.labels is not None
            else None
        )
        try:
            return db.update_project(
                conn, project_id, payload.criteria_text, payload.top_level_agent_name, labels
            )
        except db.ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except db.ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @router.get("/annotators")
    async def list_annotators() -> list[dict]:
        return db.list_annotators(conn)

    @router.post("/annotators")
    async def create_annotator(payload: AnnotatorRequest) -> dict:
        try:
            return db.create_annotator(conn, payload.name)
        except db.ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except db.ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @router.put("/annotators/{annotator_id}")
    async def rename_annotator(annotator_id: int, payload: AnnotatorRequest) -> dict:
        if db.get_annotator(conn, annotator_id) is None:
            raise HTTPException(status_code=404, detail="annotator_not_found")
        try:
            return db.rename_annotator(conn, annotator_id, payload.name)
        except db.ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except db.ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @router.delete("/annotators/{annotator_id}", status_code=204)
    async def delete_annotator(annotator_id: int) -> None:
        if db.get_annotator(conn, annotator_id) is None:
            raise HTTPException(status_code=404, detail="annotator_not_found")
        try:
            db.delete_annotator(conn, annotator_id)
        except db.ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @router.get("/projects/{project_id}/interactions")
    async def list_interactions(project_id: int, annotator_id: int, cursor: str | None = None) -> dict:
        project = db.get_project(conn, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project_not_found")
        if db.get_annotator(conn, annotator_id) is None:
            raise HTTPException(status_code=400, detail="unknown_annotator_id")

        try:
            interactions, next_cursor = await fetch_project_interactions(
                source_settings.read_token, project["top_level_agent_name"], cursor, PAGE_SIZE
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        items = []
        for interaction in interactions:
            annotation = db.get_annotation(conn, project_id, interaction.trace_id, interaction.span_id, annotator_id)
            items.append({**asdict(interaction), "annotation": annotation})

        return {"items": items, "next_cursor": next_cursor}

    @router.put("/projects/{project_id}/annotations/{trace_id}/{span_id}")
    async def upsert_annotation(
        project_id: int, trace_id: str, span_id: str, payload: AnnotationUpdateRequest
    ) -> dict:
        if db.get_project(conn, project_id) is None:
            raise HTTPException(status_code=404, detail="project_not_found")
        try:
            annotation = db.upsert_annotation(
                conn, project_id, trace_id, span_id, payload.annotator_id, payload.label_id, payload.description,
            )
        except db.ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        annotator = db.get_annotator(conn, payload.annotator_id)
        label = db.get_label(conn, payload.label_id) if payload.label_id else None
        try:
            # force_flush() blocks for up to 3s — run off the event loop so one slow
            # write-back can't stall every other concurrent request.
            await to_thread.run_sync(writer.write, annotation, annotator, label)
        except Exception as exc:
            db.mark_writeback_failed(conn, annotation["id"], annotation["revision"], f"{type(exc).__name__}: {exc}")
        else:
            db.mark_writeback_written(conn, annotation["id"], annotation["revision"])
        return db.get_annotation(conn, project_id, trace_id, span_id, payload.annotator_id)

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
from annotation_studio.logfire_writer import AnnotationWriter
from annotation_studio.routes import register_routes
from annotation_studio.settings import AppSettings, SourceSettings

_STATIC_DIST = Path(__file__).parent / "static" / "dist"


def create_annotation_studio_app(
    send_to_logfire: bool | None = None,
    connection: sqlite3.Connection | None = None,
    writer: AnnotationWriter | None = None,
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

    # `writer` injection lets tests supply a fake without ever calling
    # logfire.configure(local=True, ...) in the test suite.
    active_writer = writer if writer is not None else AnnotationWriter(source_settings.write_token)

    register_routes(app, conn, source_settings, app_settings, active_writer)

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

Expected: 15 passed.

- [ ] **Step 6: Run the full backend suite**

```bash
uv run pytest apps/annotation-studio/tests/ -v
```

Expected: all tests across `test_settings.py`, `test_db.py`, `test_logfire_client.py`,
`test_logfire_writer.py`, `test_routes.py` pass.

- [ ] **Step 7: Commit**

```bash
git add apps/annotation-studio/src/annotation_studio/main.py apps/annotation-studio/src/annotation_studio/routes.py \
  apps/annotation-studio/tests/test_routes.py
git commit -m "annotation-studio: add reviewer APIs and write-back orchestration"
```

---

### Task 6: Frontend scaffold and annotator selection

No test-first cycle here — per the spec's Testing section, this app's frontend has no
unit-test suite in v1. `npm run build` succeeding (TypeScript typecheck + Vite bundle) is this
task's correctness gate, alongside manual in-browser verification.

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
- Create: `apps/annotation-studio/frontend/src/annotator.tsx`
- Create: `apps/annotation-studio/frontend/src/pages/Annotators.tsx`
- Create: `apps/annotation-studio/frontend/src/pages/ProjectList.tsx`
- Create: `apps/annotation-studio/frontend/src/pages/ProjectDetail.tsx` (placeholder; built out in Task 7)
- Modify: `.gitignore`

**Interfaces:**
- Produces: `types.ts`'s `Label`, `ProjectSummary`, `Project`, `Annotator`, `Annotation`,
  `MessagePart`, `Message`, `Interaction`, `InteractionsPage`; `api.ts`'s `listProjects`,
  `getProject`, `updateProject`, `listAnnotators`, `createAnnotator`, `renameAnnotator`,
  `deleteAnnotator`, `listInteractions`, `upsertAnnotation`; `annotator.tsx`'s
  `AnnotatorProvider`/`useAnnotator()`. Task 7 builds `ProjectDetail`'s real content and
  grading UI against these.

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

export interface Annotator {
  id: number;
  name: string;
  created_at: string;
  updated_at: string;
}

export interface Annotation {
  id: number;
  label_id: number | null;
  description: string;
  annotator_id: number;
  revision: number;
  writeback_status: "pending" | "written" | "failed";
  writeback_error: string | null;
  written_at: string | null;
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
import type { Annotation, Annotator, InteractionsPage, Project, ProjectSummary } from "./types";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`${options?.method ?? "GET"} ${path} failed: ${response.status} ${body}`);
  }
  if (response.status === 204) return undefined as T;
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
  payload: {
    criteria_text?: string;
    top_level_agent_name?: string;
    labels?: { id: number | null; name: string }[];
  },
): Promise<Project> {
  return request<Project>(`/api/projects/${projectId}`, { method: "PUT", body: JSON.stringify(payload) });
}

export function listAnnotators(): Promise<Annotator[]> {
  return request<Annotator[]>("/api/annotators");
}

export function createAnnotator(name: string): Promise<Annotator> {
  return request<Annotator>("/api/annotators", { method: "POST", body: JSON.stringify({ name }) });
}

export function renameAnnotator(id: number, name: string): Promise<Annotator> {
  return request<Annotator>(`/api/annotators/${id}`, { method: "PUT", body: JSON.stringify({ name }) });
}

export function deleteAnnotator(id: number): Promise<void> {
  return request<void>(`/api/annotators/${id}`, { method: "DELETE" });
}

export function listInteractions(
  projectId: number,
  annotatorId: number,
  cursor: string | null,
): Promise<InteractionsPage> {
  const params = new URLSearchParams({ annotator_id: String(annotatorId) });
  if (cursor) params.set("cursor", cursor);
  return request<InteractionsPage>(`/api/projects/${projectId}/interactions?${params.toString()}`);
}

export function upsertAnnotation(
  projectId: number,
  traceId: string,
  spanId: string,
  payload: { annotator_id: number; label_id: number | null; description: string },
): Promise<Annotation> {
  return request<Annotation>(`/api/projects/${projectId}/annotations/${traceId}/${spanId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}
```

- [ ] **Step 7: Create `frontend/src/annotator.tsx`** (local-identity selection, persisted in
  `localStorage` — this is convenience state, not authentication)

```tsx
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import { listAnnotators } from "./api";
import type { Annotator } from "./types";

const STORAGE_KEY = "annotation-studio.annotator-id";

interface AnnotatorContextValue {
  annotators: Annotator[];
  selectedId: number | null;
  setSelectedId: (id: number | null) => void;
  refresh: () => void;
}

const AnnotatorContext = createContext<AnnotatorContextValue | null>(null);

function readStoredId(): number | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? Number(raw) : null;
  } catch {
    return null;
  }
}

export function AnnotatorProvider({ children }: { children: ReactNode }) {
  const [annotators, setAnnotators] = useState<Annotator[]>([]);
  const [selectedId, setSelectedIdState] = useState<number | null>(readStoredId);

  const refresh = () => {
    listAnnotators().then(setAnnotators);
  };

  useEffect(refresh, []);

  useEffect(() => {
    // If the stored profile was deleted (e.g. from another tab), clear the selection
    // rather than keep pointing at an id that no longer exists.
    if (selectedId !== null && annotators.length > 0 && !annotators.some((a) => a.id === selectedId)) {
      setSelectedIdState(null);
    }
  }, [annotators, selectedId]);

  const setSelectedId = (id: number | null) => {
    setSelectedIdState(id);
    try {
      if (id === null) localStorage.removeItem(STORAGE_KEY);
      else localStorage.setItem(STORAGE_KEY, String(id));
    } catch {
      // localStorage unavailable — selection just won't survive a reload.
    }
  };

  return (
    <AnnotatorContext.Provider value={{ annotators, selectedId, setSelectedId, refresh }}>
      {children}
    </AnnotatorContext.Provider>
  );
}

export function useAnnotator(): AnnotatorContextValue {
  const value = useContext(AnnotatorContext);
  if (!value) throw new Error("useAnnotator must be used within AnnotatorProvider");
  return value;
}
```

- [ ] **Step 8: Create `frontend/src/pages/Annotators.tsx`**

```tsx
import { useState } from "react";

import { createAnnotator, deleteAnnotator, renameAnnotator } from "../api";
import { useAnnotator } from "../annotator";

export function Annotators() {
  const { annotators, selectedId, setSelectedId, refresh } = useAnnotator();
  const [newName, setNewName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const handleCreate = async () => {
    setError(null);
    try {
      await createAnnotator(newName.trim());
      setNewName("");
      refresh();
    } catch (err) {
      setError(String(err));
    }
  };

  const handleRename = async (id: number, name: string) => {
    setError(null);
    try {
      await renameAnnotator(id, name);
      refresh();
    } catch (err) {
      setError(String(err));
    }
  };

  const handleDelete = async (id: number) => {
    setError(null);
    try {
      await deleteAnnotator(id);
      refresh();
    } catch (err) {
      setError(String(err));
    }
  };

  return (
    <div className="annotators-page">
      <h1>Choose annotator</h1>
      {error && <p className="error">{error}</p>}
      {annotators.map((annotator) => (
        <div key={annotator.id} className="annotator-row">
          <button
            className={annotator.id === selectedId ? "selected" : ""}
            onClick={() => setSelectedId(annotator.id)}
          >
            {annotator.id === selectedId ? "Selected" : "Select"}
          </button>
          <input
            defaultValue={annotator.name}
            onBlur={(e) => {
              const value = e.target.value.trim();
              if (value && value !== annotator.name) handleRename(annotator.id, value);
            }}
          />
          <button onClick={() => handleDelete(annotator.id)}>Remove</button>
        </div>
      ))}
      <div className="annotator-row">
        <input
          placeholder="New annotator name"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleCreate()}
        />
        <button onClick={handleCreate} disabled={!newName.trim()}>
          Add
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 9: Create `frontend/src/pages/ProjectList.tsx`**

```tsx
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { listProjects } from "../api";
import { useAnnotator } from "../annotator";
import type { ProjectSummary } from "../types";

export function ProjectList() {
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { annotators, selectedId } = useAnnotator();
  const selectedName = annotators.find((a) => a.id === selectedId)?.name;

  useEffect(() => {
    listProjects()
      .then(setProjects)
      .catch((err: unknown) => setError(String(err)));
  }, []);

  if (error) return <p className="error">{error}</p>;
  if (projects === null) return <p>Loading…</p>;

  return (
    <div className="project-list">
      <header className="app-header">
        <h1>Annotation Studio</h1>
        <Link to="/annotators">{selectedName ?? "Choose annotator"}</Link>
      </header>
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

- [ ] **Step 10: Create a placeholder `frontend/src/pages/ProjectDetail.tsx`** (real content built in Task 7)

```tsx
export function ProjectDetail() {
  return <h1>Project</h1>;
}
```

- [ ] **Step 11: Create `frontend/src/App.tsx`**

```tsx
import { Route, Routes } from "react-router-dom";

import { Annotators } from "./pages/Annotators";
import { ProjectDetail } from "./pages/ProjectDetail";
import { ProjectList } from "./pages/ProjectList";

export function App() {
  return (
    <Routes>
      <Route path="/" element={<ProjectList />} />
      <Route path="/annotators" element={<Annotators />} />
      <Route path="/projects/:id" element={<ProjectDetail />} />
    </Routes>
  );
}
```

- [ ] **Step 12: Create `frontend/src/main.tsx`**

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { AnnotatorProvider } from "./annotator";
import { App } from "./App";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <AnnotatorProvider>
        <App />
      </AnnotatorProvider>
    </BrowserRouter>
  </StrictMode>,
);
```

- [ ] **Step 13: Create `frontend/src/index.css`**

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

.app-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1.5rem;
}

.project-card {
  display: block;
  border: 1px solid #8884;
  border-radius: 8px;
  padding: 1rem;
  margin-bottom: 1rem;
  text-decoration: none;
  color: inherit;
}

.annotator-row {
  display: flex;
  gap: 0.5rem;
  align-items: center;
  margin-bottom: 0.5rem;
}

.annotator-row button.selected {
  background: #4a90d9;
  color: white;
}

.error {
  color: #d9534f;
}
```

- [ ] **Step 14: Add `node_modules/` to `.gitignore`**

Append to the repo's `.gitignore` (the existing `dist/` entry already covers `frontend/dist/`
and `src/annotation_studio/static/dist/` since it has no leading slash):

```

# Node (annotation-studio frontend)
node_modules/

# annotation-studio local SQLite data
apps/annotation-studio/data/
```

- [ ] **Step 15: Install and build**

```bash
cd apps/annotation-studio/frontend
npm install
npm run build
```

Expected: `dist/index.html` and `dist/assets/*.js`/`*.css` produced, no TypeScript errors.

- [ ] **Step 16: Commit**

```bash
cd /Users/duncanmckinnon/Documents/code/pydantic-demos
git add apps/annotation-studio/frontend/package.json apps/annotation-studio/frontend/package-lock.json \
  apps/annotation-studio/frontend/tsconfig.json apps/annotation-studio/frontend/tsconfig.node.json \
  apps/annotation-studio/frontend/vite.config.ts apps/annotation-studio/frontend/index.html \
  apps/annotation-studio/frontend/src .gitignore
git commit -m "annotation-studio: scaffold frontend with annotator selection"
```

---

### Task 7: Project detail and grading UI

**Files:**
- Create: `apps/annotation-studio/frontend/src/components/ProjectEditor.tsx`
- Create: `apps/annotation-studio/frontend/src/components/InteractionRow.tsx`
- Modify: `apps/annotation-studio/frontend/src/pages/ProjectDetail.tsx`
- Modify: `apps/annotation-studio/frontend/src/index.css`

**Interfaces:**
- Consumes: `api.getProject`, `api.updateProject`, `api.listInteractions`,
  `api.upsertAnnotation` (Task 6), `useAnnotator()` (Task 6).

- [ ] **Step 1: Create `components/ProjectEditor.tsx`** (criteria + agent name + stable-id
  label editor, one atomic Save)

```tsx
import { useState } from "react";

import type { Label } from "../types";

interface LabelDraft {
  id: number | null;
  name: string;
}

interface Props {
  initialCriteriaText: string;
  initialAgentName: string;
  initialLabels: Label[];
  onSave: (values: { criteria_text: string; top_level_agent_name: string; labels: LabelDraft[] }) => Promise<void>;
}

export function ProjectEditor({ initialCriteriaText, initialAgentName, initialLabels, onSave }: Props) {
  const [criteriaText, setCriteriaText] = useState(initialCriteriaText);
  const [agentName, setAgentName] = useState(initialAgentName);
  const [labels, setLabels] = useState<LabelDraft[]>(initialLabels.map((l) => ({ id: l.id, name: l.name })));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const updateLabelName = (index: number, name: string) =>
    setLabels((prev) => prev.map((l, i) => (i === index ? { ...l, name } : l)));

  const removeLabel = (index: number) => setLabels((prev) => prev.filter((_, i) => i !== index));

  const moveUp = (index: number) =>
    setLabels((prev) => {
      if (index === 0) return prev;
      const next = [...prev];
      [next[index - 1], next[index]] = [next[index], next[index - 1]];
      return next;
    });

  const moveDown = (index: number) =>
    setLabels((prev) => {
      if (index === prev.length - 1) return prev;
      const next = [...prev];
      [next[index], next[index + 1]] = [next[index + 1], next[index]];
      return next;
    });

  const addLabel = () => setLabels((prev) => [...prev, { id: null, name: "New label" }]);

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      await onSave({
        criteria_text: criteriaText,
        top_level_agent_name: agentName,
        labels: labels.map((l) => ({ ...l, name: l.name.trim() })).filter((l) => l.name.length > 0),
      });
    } catch (err) {
      // Deliberately does not replace loaded state on error — the reviewer's edits stay
      // in the form so a rejected save (400/409) doesn't lose their in-progress changes.
      setError(String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="project-editor">
      <label htmlFor="agent-name">Source agent name (Logfire span name suffix)</label>
      <input id="agent-name" value={agentName} onChange={(e) => setAgentName(e.target.value)} />

      <label htmlFor="criteria-text">Grading criteria</label>
      <textarea id="criteria-text" rows={8} value={criteriaText} onChange={(e) => setCriteriaText(e.target.value)} />

      <h3>Labels</h3>
      {labels.map((label, index) => (
        <div key={label.id ?? `new-${index}`} className="label-row">
          <input value={label.name} onChange={(e) => updateLabelName(index, e.target.value)} />
          <button onClick={() => moveUp(index)} disabled={index === 0}>
            ↑
          </button>
          <button onClick={() => moveDown(index)} disabled={index === labels.length - 1}>
            ↓
          </button>
          <button onClick={() => removeLabel(index)}>Remove</button>
        </div>
      ))}
      <button onClick={addLabel}>Add label</button>

      <button onClick={handleSave} disabled={saving}>
        {saving ? "Saving…" : "Save"}
      </button>
      {error && <p className="error">{error}</p>}
    </section>
  );
}
```

- [ ] **Step 2: Create `components/InteractionRow.tsx`** (markdown input/output, raw-attribute
  fallback, full transcript, trace link, label picker, description, write-back status, save)

```tsx
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";

import { upsertAnnotation } from "../api";
import type { Interaction, Label, Message, MessagePart } from "../types";

interface Props {
  projectId: number;
  annotatorId: number;
  interaction: Interaction;
  labels: Label[];
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

export function InteractionRow({ projectId, annotatorId, interaction, labels }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [showFullConversation, setShowFullConversation] = useState(false);
  const [labelId, setLabelId] = useState<number | null>(interaction.annotation?.label_id ?? null);
  const [description, setDescription] = useState(interaction.annotation?.description ?? "");
  const [saved, setSaved] = useState(interaction.annotation);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    // The interaction/annotator this row shows can change (pagination reload, switching
    // annotator) — resync local edit state to the freshly-loaded annotation each time.
    setLabelId(interaction.annotation?.label_id ?? null);
    setDescription(interaction.annotation?.description ?? "");
    setSaved(interaction.annotation);
  }, [interaction, annotatorId]);

  const currentLabelName = labels.find((l) => l.id === saved?.label_id)?.name ?? "Ungraded";

  const handleSaveAnnotation = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      const result = await upsertAnnotation(projectId, interaction.trace_id, interaction.span_id, {
        annotator_id: annotatorId,
        label_id: labelId,
        description,
      });
      setSaved(result);
    } catch (err) {
      setSaveError(String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="interaction-row">
      <button className="interaction-summary" onClick={() => setExpanded((v) => !v)}>
        <span className="timestamp">{new Date(interaction.start_timestamp).toLocaleString()}</span>
        <span className="preview">{interaction.input_text.slice(0, 120)}</span>
        <span className="label-badge">{currentLabelName}</span>
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

          {saved?.writeback_status === "failed" && (
            <p className="writeback-warning">
              Grade saved locally, but Logfire write-back failed: {saved.writeback_error}
            </p>
          )}
          {saved?.writeback_status === "written" && <p className="writeback-ok">Written to Logfire</p>}

          <a href={interaction.trace_url} target="_blank" rel="noopener noreferrer">
            Open trace in Logfire ↗
          </a>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Implement `pages/ProjectDetail.tsx`** (gates on a selected annotator; resets
  interactions when project or annotator changes)

```tsx
import { useCallback, useEffect, useState } from "react";
import { Navigate, useParams } from "react-router-dom";

import { getProject, listInteractions, updateProject } from "../api";
import { useAnnotator } from "../annotator";
import { InteractionRow } from "../components/InteractionRow";
import { ProjectEditor } from "../components/ProjectEditor";
import type { Interaction, Project } from "../types";

export function ProjectDetail() {
  const { id } = useParams<{ id: string }>();
  const projectId = Number(id);
  const { selectedId } = useAnnotator();

  const [project, setProject] = useState<Project | null>(null);
  const [interactions, setInteractions] = useState<Interaction[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadProject = useCallback(() => {
    getProject(projectId)
      .then(setProject)
      .catch((err: unknown) => setError(String(err)));
  }, [projectId]);

  const loadInteractions = useCallback(
    (cursor: string | null) => {
      if (selectedId === null) return;
      setLoading(true);
      listInteractions(projectId, selectedId, cursor)
        .then((page) => {
          setInteractions((prev) => (cursor ? [...prev, ...page.items] : page.items));
          setNextCursor(page.next_cursor);
        })
        .catch((err: unknown) => setError(String(err)))
        .finally(() => setLoading(false));
    },
    [projectId, selectedId],
  );

  useEffect(() => {
    loadProject();
  }, [loadProject]);

  useEffect(() => {
    // Re-fetch from page 1 whenever the project or the selected annotator changes — a
    // different annotator has different existing grades merged into each interaction.
    setInteractions([]);
    setNextCursor(null);
    loadInteractions(null);
  }, [projectId, selectedId]); // eslint-disable-line react-hooks/exhaustive-deps

  if (selectedId === null) return <Navigate to="/annotators" replace />;
  if (error) return <p className="error">{error}</p>;
  if (project === null) return <p>Loading…</p>;

  return (
    <div className="project-detail">
      <h1>{project.name}</h1>
      <ProjectEditor
        initialCriteriaText={project.criteria_text}
        initialAgentName={project.top_level_agent_name}
        initialLabels={project.labels}
        onSave={async (values) => {
          const updated = await updateProject(projectId, values);
          setProject(updated);
        }}
      />

      <h2>Interactions</h2>
      {interactions.map((interaction) => (
        <InteractionRow
          key={`${interaction.trace_id}:${interaction.span_id}`}
          projectId={projectId}
          annotatorId={selectedId}
          interaction={interaction}
          labels={project.labels}
        />
      ))}
      {nextCursor && (
        <button onClick={() => loadInteractions(nextCursor)} disabled={loading}>
          {loading ? "Loading…" : "Load more"}
        </button>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Add styling** — append to `frontend/src/index.css`

```css
.project-editor {
  border: 1px solid #8884;
  border-radius: 8px;
  padding: 1rem;
  margin-bottom: 1.5rem;
}

.project-editor textarea,
.project-editor input,
.label-row input {
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

.label-badge {
  padding: 0.15rem 0.5rem;
  border-radius: 999px;
  background: #8882;
  font-size: 0.85em;
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

.label-picker {
  display: flex;
  gap: 0.5rem;
  margin: 0.5rem 0;
}

.label-picker button.selected {
  background: #4a90d9;
  color: white;
}

.writeback-warning {
  color: #b8860b;
}

.writeback-ok {
  color: #2e7d32;
  font-size: 0.9em;
}
```

- [ ] **Step 5: Build and manually verify**

```bash
cd apps/annotation-studio/frontend
npm run build
```

With the backend running (`cp apps/annotation-studio/.env.example apps/annotation-studio/.env`,
fill in real tokens, then `uv run --package annotation-studio uvicorn annotation_studio.main:app
--reload` in one terminal) and `npm run dev` in this directory: create two annotator profiles,
grade the same interaction differently as each, confirm the badge/picker reflect only the
active annotator's grade, rename a label already in use and confirm annotations keep pointing
at it, and confirm a real write-back shows "Written to Logfire" (check the trace in the Logfire
UI for the child `annotation_studio.annotation` entry).

- [ ] **Step 6: Commit**

```bash
git add apps/annotation-studio/frontend/src/components/ProjectEditor.tsx \
  apps/annotation-studio/frontend/src/components/InteractionRow.tsx \
  apps/annotation-studio/frontend/src/pages/ProjectDetail.tsx apps/annotation-studio/frontend/src/index.css
git commit -m "annotation-studio: implement project editor and reviewer grading UI"
```

---

### Task 8: Dockerfile, Compose service, and final integration check

**Files:**
- Create: `apps/annotation-studio/Dockerfile`
- Modify: `docker-compose.yml`

**Interfaces:**
- None — this task wires existing pieces (Tasks 1–7) into the repo's Docker/Compose
  conventions and runs the full verification checklist.

- [ ] **Step 1: Create `apps/annotation-studio/Dockerfile`** (multi-stage: build the frontend,
  then copy its `dist/` into the Python image — the build context is the repo root, per
  `add-demo`'s convention, so `demo_core`'s path dependency resolves)

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

- [ ] **Step 2: Add the service to `docker-compose.yml`** (append at the end, before the
  top-level `volumes:` key — merge with the existing `volumes:` block if one already exists
  there)

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

- [ ] **Step 3: Run the full verification checklist**

```bash
uv sync --all-packages
uv run pytest apps/annotation-studio/tests/ -v
cd apps/annotation-studio/frontend && npm run build && cd /Users/duncanmckinnon/Documents/code/pydantic-demos
cp apps/annotation-studio/.env.example apps/annotation-studio/.env
docker compose --profile annotation-studio config
docker compose --profile annotation-studio build annotation-studio
```

Expected: all backend tests pass, frontend builds cleanly, `docker compose config` resolves
the `annotation-studio` service (and its `annotation_studio_data` volume) without error, and
the image builds successfully. `apps/annotation-studio/.env` now exists locally (gitignored)
with empty credential values — fill in real `LOGFIRE_TOKEN`, `RX_ASSISTANT_LOGFIRE_READ_TOKEN`,
and `RX_ASSISTANT_LOGFIRE_WRITE_TOKEN` before actually running the container.

- [ ] **Step 4: Manual end-to-end check with real credentials**

With real gitignored tokens in place, run the app (Docker or local dev), create an annotator,
grade an interaction, then grade the *same* interaction again with a different label (revision
2). In the Logfire UI, open the source trace and confirm two `annotation_studio.annotation`
child entries exist under the graded `invoke_agent` span with distinct, stable event keys,
correct annotator tags, and that switching to a second annotator profile shows an independent,
initially-ungraded state for the same interaction.

- [ ] **Step 5: Commit**

```bash
git add apps/annotation-studio/Dockerfile docker-compose.yml
git commit -m "annotation-studio: add Dockerfile and Compose service"
```

---

## Self-Review

**Spec coverage:** every section of `docs/superpowers/specs/2026-08-28-annotation-studio-design.md`
maps onto a task above — read access + keyset pagination (Task 3), the exact schema incl.
`annotators`/`revision`/`writeback_*`/`written_at` (Task 2), write-back mechanics incl. the
verified propagator fix (Task 4), the full API surface incl. annotator CRUD and
`annotator_id`-scoped interactions (Task 5), the frontend incl. annotator gating and write-back
status display (Tasks 6–7), and Docker/Compose (Task 8). No write-back onto a still-completed
span's own attributes, no Logfire annotation-queue integration, and no auth appear anywhere —
consistent with Out of Scope.

**Placeholder scan:** no TBD/TODO markers; every step has runnable code or an exact shell
command; no "see original Task N" or "similar to" references — this document is now the only
copy of this plan.

**Type consistency:** `db.py`'s `LabelInput`/`Cursor`/`Interaction` fields match
`routes.py`'s Pydantic models and `asdict()` usage, which match `types.ts`'s TypeScript
interfaces and `api.ts`'s payload shapes, which match every component's props and `onSave`
calls, checked end-to-end from Task 2 through Task 7.
