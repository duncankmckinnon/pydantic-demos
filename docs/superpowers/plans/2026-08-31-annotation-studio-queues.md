# Annotation Queues Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `annotation-studio`'s single fixed-query project with per-project **annotation queues** — each owning its own SQL query, criteria/labels, assigned annotators, and sampling percentage — plus a Logfire Explore preview link and dataset export to Logfire's hosted datasets API.

**Architecture:** SQLite schema moves criteria/labels/annotations from `projects` to a new `queues` table; queue membership (`queue_items`) is populated by a pull-based refresh with deterministic per-item sampling so a queue can grow over time without items ever dropping out. The Logfire query layer gains query validation, arbitrary-query execution, and batched content re-fetch. A new module pushes annotated queue items to Logfire as a hosted dataset via `logfire.experimental.api_client.AsyncLogfireAPIClient`. The frontend replaces the project-detail interaction list with a queue list → queue editor → queue detail flow.

**Tech Stack:** Python (FastAPI, stdlib `sqlite3`, `logfire.experimental.query_client.AsyncLogfireQueryClient`, `logfire.experimental.api_client.AsyncLogfireAPIClient`, `pydantic_evals.Case`/`Dataset`), React + TypeScript + Vite.

**Spec:** `docs/superpowers/specs/2026-08-31-annotation-studio-queues-design.md` (and the prior `docs/superpowers/specs/2026-08-28-annotation-studio-design.md` for v1 context).

## Global Constraints

- No auth (repo-wide convention).
- This is a breaking local SQLite schema change — no migration; delete `apps/annotation-studio/data/annotation_studio.sqlite3` (or whatever `ANNOTATION_STUDIO_DATABASE_PATH` points to) before running against the new schema.
- All Logfire query interpolation of caller-controlled strings is defense-in-depth only — Logfire's query endpoint is the real read-only boundary — but must still be validated client-side per the spec's exact rules.
- Multi-project support is out of scope; exactly one seeded project remains.
- Follow existing file conventions exactly (dataclasses/plain dicts in `db.py`, `ValidationError`/`ConflictError` mapping to 400/409 in `routes.py`, monkeypatch-the-module-function style in tests, no `tests/__init__.py`).

---

## Task 1: Schema rewrite + project functions

**Files:**
- Modify: `apps/annotation-studio/src/annotation_studio/db.py`
- Test: `apps/annotation-studio/tests/test_db.py`

**Interfaces:**
- Produces: `SCHEMA_SQL` (full new schema — all tables), `ValidationError`, `ConflictError`, `LabelInput` (unchanged), `get_connection`, `init_db` (unchanged), `seed_default_project(conn) -> None`, `list_projects(conn) -> list[dict]`, `get_project(conn, project_id) -> dict | None`, `update_project(conn, project_id, name: str) -> dict`.

- [ ] **Step 1: Write failing tests for the new schema's project functions**

Replace the top of `apps/annotation-studio/tests/test_db.py` (the `_fresh_conn`/`_seeded_project`/`_label_id` helpers and the project-seeding/update tests) with:

```python
def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    db.init_db(conn)
    return conn


def _seeded_project(conn: sqlite3.Connection) -> dict:
    db.seed_default_project(conn)
    return db.list_projects(conn)[0]


def test_seed_default_project_creates_project() -> None:
    conn = _fresh_conn()

    project = _seeded_project(conn)

    assert project["name"] == "rx-assistant"


def test_seed_default_project_is_idempotent() -> None:
    conn = _fresh_conn()
    db.seed_default_project(conn)

    db.seed_default_project(conn)

    assert len(db.list_projects(conn)) == 1


def test_update_project_renames() -> None:
    conn = _fresh_conn()
    project = _seeded_project(conn)

    updated = db.update_project(conn, project["id"], "renamed")

    assert updated["name"] == "renamed"
    assert db.get_project(conn, project["id"])["name"] == "renamed"
```

Delete every other test in the file for now (labels/annotators/annotations tests) — they get rewritten against the new schema in Tasks 2–4. Keep the annotator tests (`test_create_annotator_and_reject_case_insensitive_duplicate`, `test_rename_annotator_preserves_id`, `test_delete_unused_annotator_succeeds`) as-is; they don't reference projects/labels.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --package annotation-studio pytest apps/annotation-studio/tests/test_db.py -v`
Expected: FAIL — `db.seed_default_project()` takes a required `top_level_agent_name` argument that no longer matches the call, and `db.update_project()`'s signature doesn't match.

- [ ] **Step 3: Rewrite the schema and project functions**

Replace `SCHEMA_SQL` in `apps/annotation-studio/src/annotation_studio/db.py` with:

```python
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS queues (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    name TEXT NOT NULL,
    query TEXT NOT NULL,
    criteria_text TEXT NOT NULL DEFAULT '',
    sampling_percentage INTEGER NOT NULL DEFAULT 100,
    last_refreshed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, name)
);

CREATE TABLE IF NOT EXISTS labels (
    id INTEGER PRIMARY KEY,
    queue_id INTEGER NOT NULL REFERENCES queues(id),
    name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    UNIQUE(queue_id, name)
);

CREATE TABLE IF NOT EXISTS queue_annotators (
    queue_id INTEGER NOT NULL REFERENCES queues(id),
    annotator_id INTEGER NOT NULL REFERENCES annotators(id),
    PRIMARY KEY (queue_id, annotator_id)
);

CREATE TABLE IF NOT EXISTS annotators (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS queue_items (
    id INTEGER PRIMARY KEY,
    queue_id INTEGER NOT NULL REFERENCES queues(id),
    trace_id TEXT NOT NULL,
    span_id TEXT NOT NULL,
    start_timestamp TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    UNIQUE(queue_id, trace_id, span_id)
);

CREATE TABLE IF NOT EXISTS annotations (
    id INTEGER PRIMARY KEY,
    queue_id INTEGER NOT NULL REFERENCES queues(id),
    trace_id TEXT NOT NULL,
    span_id TEXT NOT NULL,
    annotator_id INTEGER NOT NULL REFERENCES annotators(id),
    label_id INTEGER REFERENCES labels(id),
    description TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL DEFAULT 1,
    writeback_status TEXT NOT NULL DEFAULT 'pending',
    writeback_error TEXT,
    written_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(queue_id, trace_id, span_id, annotator_id)
);
"""
```

Replace `seed_default_project` with:

```python
def seed_default_project(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT id FROM projects LIMIT 1").fetchone() is not None:
        return
    now = _now()
    conn.execute(
        "INSERT INTO projects (name, created_at, updated_at) VALUES (?, ?, ?)",
        ("rx-assistant", now, now),
    )
    conn.commit()
```

(Queue seeding is added in Task 2, once `create_queue` exists — a project with no queues yet is a valid intermediate state within this task.)

Replace `update_project` (which previously also touched `criteria_text`/`top_level_agent_name`/labels) with:

```python
def update_project(conn: sqlite3.Connection, project_id: int, name: str) -> dict:
    name = name.strip()
    if not name:
        raise ValidationError("Project name cannot be empty")
    conn.execute(
        "UPDATE projects SET name = ?, updated_at = ? WHERE id = ?", (name, _now(), project_id)
    )
    conn.commit()
    return get_project(conn, project_id)
```

`list_projects` and `get_project` are unchanged (they already just `SELECT *`).

Remove the now-unused `import` of `validate_agent_name` from the top of `db.py` — it's no longer called from here (it moves to being used only by `create_queue`'s seed call site in Task 2's `main.py` wiring... actually it stays used by `logfire_client.py` itself and by the seed call in `main.py`; `db.py` no longer needs it directly).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --package annotation-studio pytest apps/annotation-studio/tests/test_db.py -v`
Expected: PASS for the three project tests and the three unchanged annotator tests. The old label/annotation tests you deleted in Step 1 are gone, so nothing else runs yet.

- [ ] **Step 5: Commit**

```bash
git add apps/annotation-studio/src/annotation_studio/db.py apps/annotation-studio/tests/test_db.py
git commit -m "annotation-studio: rewrite schema for queues, simplify project functions"
```

---

## Task 2: Queue CRUD + default queue seeding

**Files:**
- Modify: `apps/annotation-studio/src/annotation_studio/db.py`
- Test: `apps/annotation-studio/tests/test_db.py`

**Interfaces:**
- Consumes: `SCHEMA_SQL`, `ValidationError`, `ConflictError`, `LabelInput`, `_now()`, `_row_to_dict()` (all from Task 1/existing `db.py`).
- Produces: `create_queue(conn, project_id, name, query, criteria_text, sampling_percentage, labels: list[LabelInput], annotator_ids: list[int]) -> dict`, `get_queue(conn, queue_id) -> dict | None`, `list_queues(conn, project_id) -> list[dict]`, `update_queue(conn, queue_id, name=None, query=None, criteria_text=None, sampling_percentage=None, labels=None, annotator_ids=None) -> dict`, `delete_queue(conn, queue_id) -> None`, `seed_default_queue(conn, project_id, top_level_agent_name: str) -> None`. Every queue dict returned by `get_queue`/`list_queues`/`create_queue`/`update_queue` includes `labels: list[dict]`, `annotator_ids: list[int]`, and (for `list_queues` only) `item_count: int`.

- [ ] **Step 1: Write failing tests**

Append to `apps/annotation-studio/tests/test_db.py`:

```python
def _queue(conn: sqlite3.Connection, project_id: int, **overrides) -> dict:
    defaults = dict(
        name="Agent turns",
        query="SELECT trace_id, span_id, start_timestamp, attributes FROM records "
              "WHERE span_name = 'invoke_agent rx_assistant_agent' ORDER BY start_timestamp DESC",
        criteria_text="",
        sampling_percentage=100,
        labels=[db.LabelInput(None, "Pass"), db.LabelInput(None, "Fail")],
        annotator_ids=[],
    )
    defaults.update(overrides)
    return db.create_queue(conn, project_id, **defaults)


def test_create_queue_returns_labels_and_annotator_ids() -> None:
    conn = _fresh_conn()
    project = _seeded_project(conn)
    ada = db.create_annotator(conn, "Ada")

    queue = _queue(conn, project["id"], annotator_ids=[ada["id"]])

    assert [l["name"] for l in queue["labels"]] == ["Pass", "Fail"]
    assert queue["annotator_ids"] == [ada["id"]]
    assert queue["sampling_percentage"] == 100


def test_create_queue_rejects_duplicate_name_in_same_project() -> None:
    conn = _fresh_conn()
    project = _seeded_project(conn)
    _queue(conn, project["id"], name="Dup")

    with pytest.raises(db.ConflictError):
        _queue(conn, project["id"], name="Dup")


def test_list_queues_includes_item_count() -> None:
    conn = _fresh_conn()
    project = _seeded_project(conn)
    queue = _queue(conn, project["id"])
    now = "2026-01-01T00:00:00"
    conn.execute(
        "INSERT INTO queue_items (queue_id, trace_id, span_id, start_timestamp, discovered_at) "
        "VALUES (?, 't1', 's1', ?, ?)",
        (queue["id"], now, now),
    )
    conn.commit()

    listed = db.list_queues(conn, project["id"])

    assert listed[0]["item_count"] == 1


def test_update_queue_renames_and_preserves_other_fields() -> None:
    conn = _fresh_conn()
    project = _seeded_project(conn)
    queue = _queue(conn, project["id"])

    updated = db.update_queue(conn, queue["id"], name="Renamed")

    assert updated["name"] == "Renamed"
    assert updated["sampling_percentage"] == 100


def test_update_queue_labels_rename_reorder_preserves_ids() -> None:
    conn = _fresh_conn()
    project = _seeded_project(conn)
    queue = _queue(conn, project["id"])
    pass_id = next(l["id"] for l in queue["labels"] if l["name"] == "Pass")

    updated = db.update_queue(
        conn, queue["id"],
        labels=[db.LabelInput(None, "New"), db.LabelInput(pass_id, "Approved")],
    )

    assert [l["name"] for l in updated["labels"]] == ["New", "Approved"]
    assert next(l["id"] for l in updated["labels"] if l["name"] == "Approved") == pass_id


def test_update_queue_rejects_label_removal_in_use() -> None:
    conn = _fresh_conn()
    project = _seeded_project(conn)
    queue = _queue(conn, project["id"])
    ada = db.create_annotator(conn, "Ada")
    pass_id = next(l["id"] for l in queue["labels"] if l["name"] == "Pass")
    db.upsert_annotation(conn, queue["id"], "t1", "s1", ada["id"], pass_id, "why")

    with pytest.raises(db.ConflictError):
        db.update_queue(conn, queue["id"], labels=[db.LabelInput(None, "OnlyOne")])


def test_update_queue_annotator_ids_replaces_assignment() -> None:
    conn = _fresh_conn()
    project = _seeded_project(conn)
    ada = db.create_annotator(conn, "Ada")
    grace = db.create_annotator(conn, "Grace")
    queue = _queue(conn, project["id"], annotator_ids=[ada["id"]])

    updated = db.update_queue(conn, queue["id"], annotator_ids=[grace["id"]])

    assert updated["annotator_ids"] == [grace["id"]]


def test_delete_queue_removes_queue_and_dependents_even_if_annotated() -> None:
    conn = _fresh_conn()
    project = _seeded_project(conn)
    queue = _queue(conn, project["id"])
    ada = db.create_annotator(conn, "Ada")
    pass_id = next(l["id"] for l in queue["labels"] if l["name"] == "Pass")
    db.upsert_annotation(conn, queue["id"], "t1", "s1", ada["id"], pass_id, "why")

    db.delete_queue(conn, queue["id"])

    assert db.get_queue(conn, queue["id"]) is None
    assert db.list_labels(conn, queue["id"]) == []


def test_seed_default_queue_creates_starter_labels_and_query() -> None:
    conn = _fresh_conn()
    project = _seeded_project(conn)

    db.seed_default_queue(conn, project["id"], "rx_assistant_agent")

    queue = db.list_queues(conn, project["id"])[0]
    assert [l["name"] for l in queue["labels"]] == ["Pass", "Neutral", "Fail"]
    assert "invoke_agent rx_assistant_agent" in queue["query"]
    assert queue["annotator_ids"] == []


def test_seed_default_queue_is_idempotent() -> None:
    conn = _fresh_conn()
    project = _seeded_project(conn)
    db.seed_default_queue(conn, project["id"], "rx_assistant_agent")

    db.seed_default_queue(conn, project["id"], "rx_assistant_agent")

    assert len(db.list_queues(conn, project["id"])) == 1
```

Note `list_labels`/`upsert_annotation` above are the queue-scoped versions this task's tests borrow from Tasks 3/4 by name — they don't exist yet, so this file won't fully pass until Task 4 is done. That's expected: **run only the tests defined in this task** in Step 2/4 below (`-k` filter), not the whole file, since Tasks 3 and 4 haven't been written yet.

- [ ] **Step 2: Run this task's tests to verify they fail**

Run: `uv run --package annotation-studio pytest apps/annotation-studio/tests/test_db.py -k "queue" -v`
Expected: FAIL — `db.create_queue` etc. don't exist yet (`AttributeError`).

- [ ] **Step 3: Implement queue CRUD**

Add to `apps/annotation-studio/src/annotation_studio/db.py` (after the project functions):

```python
def _label_rows_to_dicts(conn: sqlite3.Connection, queue_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM labels WHERE queue_id = ? ORDER BY sort_order", (queue_id,)
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def _annotator_ids_for_queue(conn: sqlite3.Connection, queue_id: int) -> list[int]:
    rows = conn.execute(
        "SELECT annotator_id FROM queue_annotators WHERE queue_id = ? ORDER BY annotator_id",
        (queue_id,),
    ).fetchall()
    return [row["annotator_id"] for row in rows]


def _set_queue_labels(conn: sqlite3.Connection, queue_id: int, labels: list[LabelInput]) -> None:
    existing_ids = {
        row["id"] for row in conn.execute("SELECT id FROM labels WHERE queue_id = ?", (queue_id,)).fetchall()
    }
    keep_ids: set[int] = set()
    for label in labels:
        if label.id is not None:
            if label.id not in existing_ids:
                raise ValidationError(f"Label {label.id} does not belong to this queue")
            keep_ids.add(label.id)
    for removed_id in existing_ids - keep_ids:
        conn.execute("DELETE FROM labels WHERE id = ?", (removed_id,))
    for order, label in enumerate(labels):
        if label.id is not None:
            conn.execute(
                "UPDATE labels SET name = ?, sort_order = ? WHERE id = ?", (label.name, order, label.id)
            )
        else:
            conn.execute(
                "INSERT INTO labels (queue_id, name, sort_order) VALUES (?, ?, ?)",
                (queue_id, label.name, order),
            )


def _set_queue_annotators(conn: sqlite3.Connection, queue_id: int, annotator_ids: list[int]) -> None:
    conn.execute("DELETE FROM queue_annotators WHERE queue_id = ?", (queue_id,))
    for annotator_id in annotator_ids:
        conn.execute(
            "INSERT INTO queue_annotators (queue_id, annotator_id) VALUES (?, ?)", (queue_id, annotator_id)
        )


def get_queue(conn: sqlite3.Connection, queue_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM queues WHERE id = ?", (queue_id,)).fetchone()
    if row is None:
        return None
    queue = _row_to_dict(row)
    queue["labels"] = _label_rows_to_dicts(conn, queue_id)
    queue["annotator_ids"] = _annotator_ids_for_queue(conn, queue_id)
    return queue


def list_queues(conn: sqlite3.Connection, project_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT q.*, (SELECT COUNT(*) FROM queue_items qi WHERE qi.queue_id = q.id) AS item_count "
        "FROM queues q WHERE q.project_id = ? ORDER BY q.id",
        (project_id,),
    ).fetchall()
    queues = []
    for row in rows:
        queue = _row_to_dict(row)
        queue["labels"] = _label_rows_to_dicts(conn, queue["id"])
        queue["annotator_ids"] = _annotator_ids_for_queue(conn, queue["id"])
        queues.append(queue)
    return queues


def create_queue(
    conn: sqlite3.Connection,
    project_id: int,
    name: str,
    query: str,
    criteria_text: str,
    sampling_percentage: int,
    labels: list[LabelInput],
    annotator_ids: list[int],
) -> dict:
    name = name.strip()
    if not name:
        raise ValidationError("Queue name cannot be empty")
    if not (1 <= sampling_percentage <= 100):
        raise ValidationError("sampling_percentage must be between 1 and 100")
    if any(label.id is not None for label in labels):
        raise ValidationError("New queue labels must not have an id")
    now = _now()
    try:
        cursor = conn.execute(
            "INSERT INTO queues (project_id, name, query, criteria_text, sampling_percentage, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (project_id, name, query, criteria_text, sampling_percentage, now, now),
        )
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise ConflictError(f"A queue named {name!r} already exists in this project") from exc
    queue_id = cursor.lastrowid
    for order, label in enumerate(labels):
        conn.execute(
            "INSERT INTO labels (queue_id, name, sort_order) VALUES (?, ?, ?)", (queue_id, label.name, order)
        )
    for annotator_id in annotator_ids:
        conn.execute(
            "INSERT INTO queue_annotators (queue_id, annotator_id) VALUES (?, ?)", (queue_id, annotator_id)
        )
    conn.commit()
    return get_queue(conn, queue_id)


def update_queue(
    conn: sqlite3.Connection,
    queue_id: int,
    name: str | None = None,
    query: str | None = None,
    criteria_text: str | None = None,
    sampling_percentage: int | None = None,
    labels: list[LabelInput] | None = None,
    annotator_ids: list[int] | None = None,
) -> dict:
    try:
        if name is not None:
            name = name.strip()
            if not name:
                raise ValidationError("Queue name cannot be empty")
            conn.execute("UPDATE queues SET name = ?, updated_at = ? WHERE id = ?", (name, _now(), queue_id))
        if query is not None:
            conn.execute("UPDATE queues SET query = ?, updated_at = ? WHERE id = ?", (query, _now(), queue_id))
        if criteria_text is not None:
            conn.execute(
                "UPDATE queues SET criteria_text = ?, updated_at = ? WHERE id = ?",
                (criteria_text, _now(), queue_id),
            )
        if sampling_percentage is not None:
            if not (1 <= sampling_percentage <= 100):
                raise ValidationError("sampling_percentage must be between 1 and 100")
            conn.execute(
                "UPDATE queues SET sampling_percentage = ?, updated_at = ? WHERE id = ?",
                (sampling_percentage, _now(), queue_id),
            )
        if labels is not None:
            _set_queue_labels(conn, queue_id, labels)
        if annotator_ids is not None:
            _set_queue_annotators(conn, queue_id, annotator_ids)
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise ConflictError("Cannot remove a label used by an existing annotation") from exc
    except ValidationError:
        conn.rollback()
        raise
    conn.commit()
    return get_queue(conn, queue_id)


def delete_queue(conn: sqlite3.Connection, queue_id: int) -> None:
    conn.execute("DELETE FROM annotations WHERE queue_id = ?", (queue_id,))
    conn.execute("DELETE FROM queue_items WHERE queue_id = ?", (queue_id,))
    conn.execute("DELETE FROM queue_annotators WHERE queue_id = ?", (queue_id,))
    conn.execute("DELETE FROM labels WHERE queue_id = ?", (queue_id,))
    conn.execute("DELETE FROM queues WHERE id = ?", (queue_id,))
    conn.commit()


def seed_default_queue(conn: sqlite3.Connection, project_id: int, top_level_agent_name: str) -> None:
    if conn.execute("SELECT id FROM queues WHERE project_id = ? LIMIT 1", (project_id,)).fetchone() is not None:
        return
    validate_agent_name(top_level_agent_name)
    query = (
        "SELECT trace_id, span_id, start_timestamp, duration, attributes FROM records "
        f"WHERE span_name = 'invoke_agent {top_level_agent_name}' ORDER BY start_timestamp DESC"
    )
    create_queue(
        conn, project_id,
        name="All rx_assistant interactions",
        query=query,
        criteria_text="",
        sampling_percentage=100,
        labels=[LabelInput(None, "Pass"), LabelInput(None, "Neutral"), LabelInput(None, "Fail")],
        annotator_ids=[],
    )
```

Add back `from annotation_studio.logfire_client import validate_agent_name` at the top of `db.py` (Task 1 removed it; `seed_default_queue` needs it now).

- [ ] **Step 4: Run this task's tests to verify they pass**

Run: `uv run --package annotation-studio pytest apps/annotation-studio/tests/test_db.py -k "queue" -v`
Expected: PASS, except the two tests that call `db.upsert_annotation`/`db.list_labels` with the new queue-scoped signature (`test_update_queue_rejects_label_removal_in_use`, `test_delete_queue_removes_queue_and_dependents_even_if_annotated`) — those two will fail with `TypeError`/`AttributeError` until Tasks 3–4 land `list_labels`/`upsert_annotation` against `queue_id`. Confirm the failures are only those two, for that reason, then proceed — they'll pass once Task 4 is committed on top.

- [ ] **Step 5: Commit**

```bash
git add apps/annotation-studio/src/annotation_studio/db.py apps/annotation-studio/tests/test_db.py
git commit -m "annotation-studio: add queue CRUD and default-queue seeding"
```

---

## Task 3: Queue items (membership) functions

**Files:**
- Modify: `apps/annotation-studio/src/annotation_studio/db.py`
- Test: `apps/annotation-studio/tests/test_db.py`

**Interfaces:**
- Consumes: `_now()`, `_row_to_dict()`.
- Produces: `insert_queue_items(conn, queue_id, items: list[dict]) -> int` (`items` elements are `{"trace_id": str, "span_id": str, "start_timestamp": str}`; returns count of newly inserted rows), `set_queue_last_refreshed(conn, queue_id, timestamp: str) -> None`, `list_queue_items(conn, queue_id, cursor: str | None, limit: int) -> tuple[list[dict], str | None]` (cursor is opaque, same encode/decode pattern as `logfire_client.Cursor` but keyed on local `id` — see Step 3), `list_labels(conn, queue_id) -> list[dict]` (renamed from project-scoped; same as `_label_rows_to_dicts` but public, used by routes), `get_label(conn, label_id) -> dict | None` (unchanged from v1).

- [ ] **Step 1: Write failing tests**

Append to `apps/annotation-studio/tests/test_db.py`:

```python
def test_insert_queue_items_dedupes_by_trace_and_span() -> None:
    conn = _fresh_conn()
    project = _seeded_project(conn)
    queue = _queue(conn, project["id"])
    items = [{"trace_id": "t1", "span_id": "s1", "start_timestamp": "2026-01-01T00:00:00"}]

    first = db.insert_queue_items(conn, queue["id"], items)
    second = db.insert_queue_items(conn, queue["id"], items)

    assert first == 1
    assert second == 0


def test_set_queue_last_refreshed_updates_timestamp() -> None:
    conn = _fresh_conn()
    project = _seeded_project(conn)
    queue = _queue(conn, project["id"])

    db.set_queue_last_refreshed(conn, queue["id"], "2026-01-01T00:00:00")

    assert db.get_queue(conn, queue["id"])["last_refreshed_at"] == "2026-01-01T00:00:00"


def test_list_queue_items_paginates_newest_first() -> None:
    conn = _fresh_conn()
    project = _seeded_project(conn)
    queue = _queue(conn, project["id"])
    db.insert_queue_items(conn, queue["id"], [
        {"trace_id": "t1", "span_id": "s1", "start_timestamp": "2026-01-01T00:00:00"},
        {"trace_id": "t2", "span_id": "s2", "start_timestamp": "2026-01-02T00:00:00"},
        {"trace_id": "t3", "span_id": "s3", "start_timestamp": "2026-01-03T00:00:00"},
    ])

    page1, cursor1 = db.list_queue_items(conn, queue["id"], cursor=None, limit=2)
    page2, cursor2 = db.list_queue_items(conn, queue["id"], cursor=cursor1, limit=2)

    assert [item["trace_id"] for item in page1] == ["t3", "t2"]
    assert [item["trace_id"] for item in page2] == ["t1"]
    assert cursor2 is None


def test_list_labels_is_queue_scoped() -> None:
    conn = _fresh_conn()
    project = _seeded_project(conn)
    queue_a = _queue(conn, project["id"], name="A")
    queue_b = _queue(conn, project["id"], name="B", labels=[db.LabelInput(None, "Only in B")])

    assert [l["name"] for l in db.list_labels(conn, queue_a["id"])] == ["Pass", "Fail"]
    assert [l["name"] for l in db.list_labels(conn, queue_b["id"])] == ["Only in B"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --package annotation-studio pytest apps/annotation-studio/tests/test_db.py -k "queue_item or list_labels" -v`
Expected: FAIL — functions don't exist yet.

- [ ] **Step 3: Implement**

Add to `apps/annotation-studio/src/annotation_studio/db.py`:

```python
def list_labels(conn: sqlite3.Connection, queue_id: int) -> list[dict]:
    return _label_rows_to_dicts(conn, queue_id)


def get_label(conn: sqlite3.Connection, label_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM labels WHERE id = ?", (label_id,)).fetchone()
    return _row_to_dict(row) if row else None


def insert_queue_items(conn: sqlite3.Connection, queue_id: int, items: list[dict]) -> int:
    now = _now()
    inserted = 0
    for item in items:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO queue_items (queue_id, trace_id, span_id, start_timestamp, discovered_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (queue_id, item["trace_id"], item["span_id"], item["start_timestamp"], now),
        )
        inserted += cursor.rowcount
    conn.commit()
    return inserted


def set_queue_last_refreshed(conn: sqlite3.Connection, queue_id: int, timestamp: str) -> None:
    conn.execute("UPDATE queues SET last_refreshed_at = ? WHERE id = ?", (timestamp, queue_id))
    conn.commit()


def list_queue_items(
    conn: sqlite3.Connection, queue_id: int, cursor: str | None, limit: int
) -> tuple[list[dict], str | None]:
    # Cursor is the last-shown row's local `id` (not a Logfire cursor) — queue_items is a
    # local table, so a simple exclusive `id <` predicate over `ORDER BY id DESC` is enough;
    # no keyset-on-timestamp complexity is needed since `id` is already unique and monotonic.
    params: list = [queue_id]
    sql = "SELECT * FROM queue_items WHERE queue_id = ? "
    if cursor is not None:
        sql += "AND id < ? "
        params.append(int(cursor))
    sql += "ORDER BY id DESC LIMIT ?"
    params.append(limit + 1)
    rows = [_row_to_dict(row) for row in conn.execute(sql, params).fetchall()]
    next_cursor = str(rows[limit]["id"]) if len(rows) > limit else None
    return rows[:limit], next_cursor
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --package annotation-studio pytest apps/annotation-studio/tests/test_db.py -k "queue_item or list_labels" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/annotation-studio/src/annotation_studio/db.py apps/annotation-studio/tests/test_db.py
git commit -m "annotation-studio: add queue_items membership and queue-scoped labels"
```

---

## Task 4: Annotations (queue-scoped)

**Files:**
- Modify: `apps/annotation-studio/src/annotation_studio/db.py`
- Test: `apps/annotation-studio/tests/test_db.py`

**Interfaces:**
- Produces: `get_annotation(conn, queue_id, trace_id, span_id, annotator_id) -> dict | None`, `upsert_annotation(conn, queue_id, trace_id, span_id, annotator_id, label_id, description) -> dict`, `mark_writeback_written(conn, annotation_id, revision) -> None`, `mark_writeback_failed(conn, annotation_id, revision, error) -> None`, `list_annotations_for_dataset(conn, queue_id, label_id: int | None) -> list[dict]`.

- [ ] **Step 1: Write failing tests**

Append to `apps/annotation-studio/tests/test_db.py` (these mirror the v1 annotation tests, retargeted to `queue_id`, plus the dataset-listing test):

```python
def test_two_annotators_grade_same_item_independently() -> None:
    conn = _fresh_conn()
    project = _seeded_project(conn)
    queue = _queue(conn, project["id"])
    pass_id = next(l["id"] for l in queue["labels"] if l["name"] == "Pass")
    ada = db.create_annotator(conn, "Ada")
    grace = db.create_annotator(conn, "Grace")

    a = db.upsert_annotation(conn, queue["id"], "t1", "s1", ada["id"], pass_id, "good")
    b = db.upsert_annotation(conn, queue["id"], "t1", "s1", grace["id"], pass_id, "also good")

    assert a["id"] != b["id"]
    assert db.get_annotation(conn, queue["id"], "t1", "s1", grace["id"])["id"] == b["id"]


def test_upsert_annotation_second_save_increments_revision_and_resets_writeback() -> None:
    conn = _fresh_conn()
    project = _seeded_project(conn)
    queue = _queue(conn, project["id"])
    pass_id = next(l["id"] for l in queue["labels"] if l["name"] == "Pass")
    ada = db.create_annotator(conn, "Ada")
    first = db.upsert_annotation(conn, queue["id"], "t1", "s1", ada["id"], pass_id, "one")
    db.mark_writeback_written(conn, first["id"], first["revision"])

    second = db.upsert_annotation(conn, queue["id"], "t1", "s1", ada["id"], pass_id, "two")

    assert second["revision"] == 2
    assert second["writeback_status"] == "pending"
    assert second["written_at"] is None


def test_upsert_annotation_rejects_unknown_annotator() -> None:
    conn = _fresh_conn()
    project = _seeded_project(conn)
    queue = _queue(conn, project["id"])
    pass_id = next(l["id"] for l in queue["labels"] if l["name"] == "Pass")

    with pytest.raises(ValueError):
        db.upsert_annotation(conn, queue["id"], "t1", "s1", 999, pass_id, "x")


def test_upsert_annotation_rejects_label_from_another_queue() -> None:
    conn = _fresh_conn()
    project = _seeded_project(conn)
    queue_a = _queue(conn, project["id"], name="A")
    queue_b = _queue(conn, project["id"], name="B", labels=[db.LabelInput(None, "Foreign")])
    foreign_label_id = queue_b["labels"][0]["id"]
    ada = db.create_annotator(conn, "Ada")

    with pytest.raises(ValueError):
        db.upsert_annotation(conn, queue_a["id"], "t1", "s1", ada["id"], foreign_label_id, "x")


def test_mark_writeback_written_is_a_noop_for_a_stale_revision() -> None:
    conn = _fresh_conn()
    project = _seeded_project(conn)
    queue = _queue(conn, project["id"])
    pass_id = next(l["id"] for l in queue["labels"] if l["name"] == "Pass")
    ada = db.create_annotator(conn, "Ada")
    first = db.upsert_annotation(conn, queue["id"], "t1", "s1", ada["id"], pass_id, "one")
    db.upsert_annotation(conn, queue["id"], "t1", "s1", ada["id"], pass_id, "two")

    db.mark_writeback_written(conn, first["id"], first["revision"])

    reloaded = db.get_annotation(conn, queue["id"], "t1", "s1", ada["id"])
    assert reloaded["revision"] == 2
    assert reloaded["writeback_status"] == "pending"


def test_mark_writeback_failed_sets_status_and_truncated_error() -> None:
    conn = _fresh_conn()
    project = _seeded_project(conn)
    queue = _queue(conn, project["id"])
    pass_id = next(l["id"] for l in queue["labels"] if l["name"] == "Pass")
    ada = db.create_annotator(conn, "Ada")
    annotation = db.upsert_annotation(conn, queue["id"], "t1", "s1", ada["id"], pass_id, "ok")

    db.mark_writeback_failed(conn, annotation["id"], annotation["revision"], "x" * 600)

    reloaded = db.get_annotation(conn, queue["id"], "t1", "s1", ada["id"])
    assert reloaded["writeback_status"] == "failed"
    assert len(reloaded["writeback_error"]) == 500


def test_list_annotations_for_dataset_filters_by_label_and_excludes_ungraded() -> None:
    conn = _fresh_conn()
    project = _seeded_project(conn)
    queue = _queue(conn, project["id"])
    pass_id = next(l["id"] for l in queue["labels"] if l["name"] == "Pass")
    fail_id = next(l["id"] for l in queue["labels"] if l["name"] == "Fail")
    ada = db.create_annotator(conn, "Ada")
    db.upsert_annotation(conn, queue["id"], "t1", "s1", ada["id"], pass_id, "good")
    db.upsert_annotation(conn, queue["id"], "t2", "s2", ada["id"], fail_id, "bad")
    db.upsert_annotation(conn, queue["id"], "t3", "s3", ada["id"], None, "ungraded")

    all_graded = db.list_annotations_for_dataset(conn, queue["id"], label_id=None)
    only_pass = db.list_annotations_for_dataset(conn, queue["id"], label_id=pass_id)

    assert {a["trace_id"] for a in all_graded} == {"t1", "t2"}
    assert {a["trace_id"] for a in only_pass} == {"t1"}
```

Now the two tests deferred from Task 2 (`test_update_queue_rejects_label_removal_in_use`, `test_delete_queue_removes_queue_and_dependents_even_if_annotated`) will also pass once this task's `upsert_annotation`/`list_labels` land.

- [ ] **Step 2: Run to verify failure**

Run: `uv run --package annotation-studio pytest apps/annotation-studio/tests/test_db.py -v`
Expected: FAIL on every annotation test (functions don't exist) — everything else (projects, queues, queue_items) still passes.

- [ ] **Step 3: Implement**

Add to `apps/annotation-studio/src/annotation_studio/db.py`:

```python
def get_annotation(
    conn: sqlite3.Connection, queue_id: int, trace_id: str, span_id: str, annotator_id: int
) -> dict | None:
    row = conn.execute(
        "SELECT * FROM annotations WHERE queue_id = ? AND trace_id = ? AND span_id = ? AND annotator_id = ?",
        (queue_id, trace_id, span_id, annotator_id),
    ).fetchone()
    return _row_to_dict(row) if row else None


def upsert_annotation(
    conn: sqlite3.Connection,
    queue_id: int,
    trace_id: str,
    span_id: str,
    annotator_id: int,
    label_id: int | None,
    description: str,
) -> dict:
    if get_annotator(conn, annotator_id) is None:
        raise ValidationError(f"Unknown annotator {annotator_id}")
    if label_id is not None and conn.execute(
        "SELECT 1 FROM labels WHERE id = ? AND queue_id = ?", (label_id, queue_id)
    ).fetchone() is None:
        raise ValidationError(f"Label {label_id} does not belong to this queue")

    now = _now()
    existing = get_annotation(conn, queue_id, trace_id, span_id, annotator_id)
    if existing:
        conn.execute(
            "UPDATE annotations SET label_id = ?, description = ?, revision = revision + 1, "
            "writeback_status = 'pending', writeback_error = NULL, written_at = NULL, "
            "updated_at = ? WHERE id = ?",
            (label_id, description, now, existing["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO annotations (queue_id, trace_id, span_id, annotator_id, label_id, "
            "description, revision, writeback_status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, 'pending', ?, ?)",
            (queue_id, trace_id, span_id, annotator_id, label_id, description, now, now),
        )
    conn.commit()
    return get_annotation(conn, queue_id, trace_id, span_id, annotator_id)


def mark_writeback_written(conn: sqlite3.Connection, annotation_id: int, revision: int) -> None:
    now = _now()
    conn.execute(
        "UPDATE annotations SET writeback_status = 'written', writeback_error = NULL, "
        "written_at = ?, updated_at = ? WHERE id = ? AND revision = ?",
        (now, now, annotation_id, revision),
    )
    conn.commit()


def mark_writeback_failed(conn: sqlite3.Connection, annotation_id: int, revision: int, error: str) -> None:
    conn.execute(
        "UPDATE annotations SET writeback_status = 'failed', writeback_error = ?, "
        "updated_at = ? WHERE id = ? AND revision = ?",
        (error[:500], _now(), annotation_id, revision),
    )
    conn.commit()


def list_annotations_for_dataset(conn: sqlite3.Connection, queue_id: int, label_id: int | None) -> list[dict]:
    sql = "SELECT * FROM annotations WHERE queue_id = ? AND label_id IS NOT NULL"
    params: list = [queue_id]
    if label_id is not None:
        sql += " AND label_id = ?"
        params.append(label_id)
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(row) for row in rows]
```

- [ ] **Step 4: Run the full test file to verify everything passes**

Run: `uv run --package annotation-studio pytest apps/annotation-studio/tests/test_db.py -v`
Expected: PASS — every test in the file, including the two deferred from Task 2.

- [ ] **Step 5: Commit**

```bash
git add apps/annotation-studio/src/annotation_studio/db.py apps/annotation-studio/tests/test_db.py
git commit -m "annotation-studio: retarget annotations to queues, add dataset listing"
```

---

## Task 5: Query validation and deterministic sampling

**Files:**
- Modify: `apps/annotation-studio/src/annotation_studio/logfire_client.py`
- Test: `apps/annotation-studio/tests/test_logfire_client.py`

**Interfaces:**
- Produces: `QUERY_MAX_LENGTH: int`, `validate_query(query: str) -> str` (raises `ValueError`; returns the query with a single trailing `;` stripped), `REQUIRED_QUEUE_COLUMNS: tuple[str, ...]`, `sample_included(queue_id: int, trace_id: str, span_id: str, sampling_percentage: int) -> bool`.

- [ ] **Step 1: Write failing tests**

Append to `apps/annotation-studio/tests/test_logfire_client.py`:

```python
def test_validate_query_accepts_a_valid_select() -> None:
    query = "SELECT trace_id, span_id, start_timestamp FROM records WHERE span_name = 'x'"
    assert logfire_client.validate_query(query) == query


def test_validate_query_strips_single_trailing_semicolon() -> None:
    assert logfire_client.validate_query("SELECT 1;") == "SELECT 1"


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
    # Deterministic hash-bucket sampling, not a statistical guarantee — a generous band
    # confirms it isn't systematically broken (e.g. always-true or always-false) without
    # making this test flaky.
    assert 500 < included < 900
```

Add `import pytest` to the top of the test file if not already present (it already is, per the existing file).

- [ ] **Step 2: Run to verify failure**

Run: `uv run --package annotation-studio pytest apps/annotation-studio/tests/test_logfire_client.py -k "validate_query or sample_included" -v`
Expected: FAIL — `AttributeError: module 'annotation_studio.logfire_client' has no attribute 'validate_query'`.

- [ ] **Step 3: Implement**

Add to `apps/annotation-studio/src/annotation_studio/logfire_client.py` (near the top, after the existing pattern constants):

```python
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
```

Add `import hashlib` to the imports at the top of `logfire_client.py` (it already imports `re`).

- [ ] **Step 4: Run to verify pass**

Run: `uv run --package annotation-studio pytest apps/annotation-studio/tests/test_logfire_client.py -v`
Expected: PASS — the new tests plus every pre-existing test in the file (unaffected by this addition).

- [ ] **Step 5: Commit**

```bash
git add apps/annotation-studio/src/annotation_studio/logfire_client.py apps/annotation-studio/tests/test_logfire_client.py
git commit -m "annotation-studio: add queue query validation and deterministic sampling"
```

---

## Task 6: Query dry-run validation and queue refresh fetch

**Files:**
- Modify: `apps/annotation-studio/src/annotation_studio/logfire_client.py`
- Test: `apps/annotation-studio/tests/test_logfire_client.py`

**Interfaces:**
- Consumes: `AsyncLogfireQueryClient` (already imported), `REQUIRED_QUEUE_COLUMNS` (Task 5), the `FakeQueryClient` test double already defined in the test file.
- Produces: `async def validate_query_columns(read_token: str, query: str) -> None` (raises `ValueError`), `async def fetch_queue_matches(read_token: str, query: str, min_timestamp: datetime, max_timestamp: datetime, limit: int) -> list[dict]`.

- [ ] **Step 1: Write failing tests**

Append to `apps/annotation-studio/tests/test_logfire_client.py`:

```python
class FakeErroringQueryClient(FakeQueryClient):
    async def query_json_rows(self, sql, min_timestamp=None, limit=None, **kwargs):
        raise RuntimeError("simulated Logfire query error")


async def test_validate_query_columns_passes_for_a_row_with_required_columns(monkeypatch) -> None:
    fake_client = FakeQueryClient([_row("trace-1", "2026-08-28T00:00:00Z")])
    monkeypatch.setattr(logfire_client, "AsyncLogfireQueryClient", lambda read_token: fake_client)

    await logfire_client.validate_query_columns("test-token", "SELECT * FROM records")  # does not raise

    assert "LIMIT 1" in fake_client.queries[0]["sql"]


async def test_validate_query_columns_raises_when_required_column_missing(monkeypatch) -> None:
    fake_client = FakeQueryClient([{"trace_id": "t1", "span_id": "s1"}])  # no start_timestamp
    monkeypatch.setattr(logfire_client, "AsyncLogfireQueryClient", lambda read_token: fake_client)

    with pytest.raises(ValueError, match="start_timestamp"):
        await logfire_client.validate_query_columns("test-token", "SELECT trace_id, span_id FROM records")


async def test_validate_query_columns_passes_when_query_currently_matches_nothing(monkeypatch) -> None:
    fake_client = FakeQueryClient([])
    monkeypatch.setattr(logfire_client, "AsyncLogfireQueryClient", lambda read_token: fake_client)

    await logfire_client.validate_query_columns("test-token", "SELECT * FROM records WHERE 1=0")  # does not raise


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
    fake_client = FakeQueryClient([{"trace_id": "t1"}])  # missing span_id, start_timestamp
    monkeypatch.setattr(logfire_client, "AsyncLogfireQueryClient", lambda read_token: fake_client)

    rows = await logfire_client.fetch_queue_matches(
        "test-token", "SELECT * FROM records", datetime.now(timezone.utc), datetime.now(timezone.utc), limit=100
    )

    assert rows == []
```

`FakeQueryClient.query_json_rows` in the existing test file already accepts `**kwargs`, so passing `max_timestamp=` from `fetch_queue_matches` won't break it — but it doesn't currently record `max_timestamp` into `self.queries`. That's fine; the new tests above only assert on `min_timestamp`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run --package annotation-studio pytest apps/annotation-studio/tests/test_logfire_client.py -k "validate_query_columns or fetch_queue_matches" -v`
Expected: FAIL — functions don't exist.

- [ ] **Step 3: Implement**

Add to `apps/annotation-studio/src/annotation_studio/logfire_client.py`:

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --package annotation-studio pytest apps/annotation-studio/tests/test_logfire_client.py -v`
Expected: PASS for the whole file.

- [ ] **Step 5: Commit**

```bash
git add apps/annotation-studio/src/annotation_studio/logfire_client.py apps/annotation-studio/tests/test_logfire_client.py
git commit -m "annotation-studio: add queue query dry-run validation and refresh fetch"
```

---

## Task 7: Generalize row display, batched content fetch, Explore link

**Files:**
- Modify: `apps/annotation-studio/src/annotation_studio/logfire_client.py`
- Test: `apps/annotation-studio/tests/test_logfire_client.py`

**Interfaces:**
- Consumes: `Interaction`, `parse_interaction`, `build_trace_link`, `validate_trace_and_span`, `LOOKBACK_DAYS`.
- Produces: `Interaction.raw_row: dict | None` (renamed from `raw_attributes`; holds every column from the row except `trace_id`/`span_id`/`start_timestamp` when structured parsing doesn't apply), `async def fetch_queue_item_content(read_token: str, items: list[tuple[str, str]]) -> dict[tuple[str, str], Interaction]`, `build_explore_link(base_url: str, organization_name: str, project_name: str, query: str) -> str`.

- [ ] **Step 1: Update existing tests for the `raw_row` rename, write new tests**

In `apps/annotation-studio/tests/test_logfire_client.py`, change `test_parse_interaction_falls_back_to_raw_attributes_when_messages_missing`:

```python
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
```

And change the first assertion block in `test_parse_interaction_extracts_input_and_output_for_new_turn` from `assert interaction.raw_attributes is None` to `assert interaction.raw_row is None`.

Append:

```python
async def test_fetch_queue_item_content_keys_by_trace_and_span(monkeypatch) -> None:
    fake_client = FakeQueryClient([_row("trace-1", "2026-08-28T00:00:00Z", span_id="c7a2373c3fe61d3f")])
    monkeypatch.setattr(logfire_client, "AsyncLogfireQueryClient", lambda read_token: fake_client)

    content = await logfire_client.fetch_queue_item_content(
        "test-token", [("trace-1", "c7a2373c3fe61d3f")]
    )

    assert ("trace-1", "c7a2373c3fe61d3f") in content
    assert content[("trace-1", "c7a2373c3fe61d3f")].input_text == "hi"


async def test_fetch_queue_item_content_omits_pairs_not_returned(monkeypatch) -> None:
    fake_client = FakeQueryClient([])  # simulates a trace aged out of the 14-day window
    monkeypatch.setattr(logfire_client, "AsyncLogfireQueryClient", lambda read_token: fake_client)

    content = await logfire_client.fetch_queue_item_content("test-token", [("trace-1", "c7a2373c3fe61d3f")])

    assert content == {}


async def test_fetch_queue_item_content_returns_empty_dict_for_no_items() -> None:
    assert await logfire_client.fetch_queue_item_content("test-token", []) == {}


async def test_fetch_queue_item_content_rejects_malformed_ids() -> None:
    with pytest.raises(ValueError):
        await logfire_client.fetch_queue_item_content("test-token", [("not-hex", "c7a2373c3fe61d3f")])


def test_build_explore_link_includes_project_path_and_query() -> None:
    url = logfire_client.build_explore_link(
        "https://logfire-us.pydantic.dev", "duncan", "rx-assistant-demo", "SELECT 1"
    )
    assert url.startswith("https://logfire-us.pydantic.dev/duncan/rx-assistant-demo/explore?q=")
    assert "SELECT" in url
```

Add `from datetime import datetime, timezone` to the test file's imports if not already present (Task 6 already needs `datetime`/`timezone` for its own tests — add there if this task lands first, otherwise it's already there).

- [ ] **Step 2: Run to verify failure**

Run: `uv run --package annotation-studio pytest apps/annotation-studio/tests/test_logfire_client.py -v`
Expected: FAIL on the renamed/new tests (`raw_row` doesn't exist yet; `fetch_queue_item_content`/`build_explore_link` don't exist).

- [ ] **Step 3: Implement**

In `apps/annotation-studio/src/annotation_studio/logfire_client.py`:

Change the `Interaction` dataclass's last field from `raw_attributes: dict | None = None` to `raw_row: dict | None = None`.

In `parse_interaction`, change the early-return branch:

```python
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
```

Add:

```python
async def fetch_queue_item_content(read_token: str, items: list[tuple[str, str]]) -> dict[tuple[str, str], Interaction]:
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


def build_explore_link(base_url: str, organization_name: str, project_name: str, query: str) -> str:
    # Best-effort: Logfire's docs don't confirm the Explore page reads a `q` URL param the way
    # the live view does (see build_trace_link) — if it doesn't, this just opens Explore itself,
    # which is still useful. The frontend's "Copy query" button is the reliable path.
    return f"{base_url}/{organization_name}/{project_name}/explore?q={quote(query)}"
```

`timedelta`, `datetime`, `timezone`, and `quote` are already imported at the top of `logfire_client.py` from Task-1-era code.

- [ ] **Step 4: Run to verify pass**

Run: `uv run --package annotation-studio pytest apps/annotation-studio/tests/test_logfire_client.py -v`
Expected: PASS for the whole file.

- [ ] **Step 5: Commit**

```bash
git add apps/annotation-studio/src/annotation_studio/logfire_client.py apps/annotation-studio/tests/test_logfire_client.py
git commit -m "annotation-studio: generalize raw fallback to full row, add batched content fetch and Explore link"
```

---

## Task 8: Writer uses queue_id

**Files:**
- Modify: `apps/annotation-studio/src/annotation_studio/logfire_writer.py`
- Test: `apps/annotation-studio/tests/test_logfire_writer.py`

**Interfaces:**
- No signature changes — `AnnotationWriter.write(annotation: dict, annotator: dict, label: dict | None) -> None` still takes the same shapes; only the key it reads off `annotation` and the attribute name it emits change from `project_id` to `queue_id`.

- [ ] **Step 1: Update the failing test**

In `apps/annotation-studio/tests/test_logfire_writer.py`, change the `annotation()` helper:

```python
def annotation():
    return {"id": 11, "revision": 2, "trace_id": "01a045b8d6d40acd6c98ee00f1a3fe93",
            "span_id": "c7a2373c3fe61d3f", "queue_id": 1, "description": "Grounded"}
```

Add a new test:

```python
def test_emits_queue_id_not_project_id(fake_logfire):
    AnnotationWriter("write", client=fake_logfire).write(annotation(), annotator(), label())
    attrs = fake_logfire.events[0]
    assert attrs["queue_id"] == 1
    assert "project_id" not in attrs
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --package annotation-studio pytest apps/annotation-studio/tests/test_logfire_writer.py -v`
Expected: FAIL — `AnnotationWriter.write` currently does `project_id=annotation["project_id"]`, which now `KeyError`s since the test helper no longer provides that key.

- [ ] **Step 3: Implement**

In `apps/annotation-studio/src/annotation_studio/logfire_writer.py`, change:

```python
                project_id=annotation["project_id"],
```

to:

```python
                queue_id=annotation["queue_id"],
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --package annotation-studio pytest apps/annotation-studio/tests/test_logfire_writer.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/annotation-studio/src/annotation_studio/logfire_writer.py apps/annotation-studio/tests/test_logfire_writer.py
git commit -m "annotation-studio: write back queue_id instead of project_id"
```

---

## Task 9: Project and queue CRUD routes

**Files:**
- Modify: `apps/annotation-studio/src/annotation_studio/routes.py`, `apps/annotation-studio/src/annotation_studio/main.py`
- Test: `apps/annotation-studio/tests/test_routes.py`

**Interfaces:**
- Consumes: `db.list_projects/get_project/update_project`, `db.create_queue/get_queue/list_queues/update_queue/delete_queue/seed_default_queue` (Tasks 1–2), `db.LabelInput`, `logfire_client.validate_query` (Task 5), `logfire_client.validate_query_columns` (Task 6, awaited via `anyio.to_thread`... actually it's already async, call directly), `source_settings.read_token`.
- Produces: FastAPI routes `GET/PUT /api/projects/{id}`, `GET /api/projects/{id}/queues`, `POST /api/projects/{id}/queues`, `GET/PUT/DELETE /api/queues/{id}`.

- [ ] **Step 1: Rewrite the project and add queue tests**

In `apps/annotation-studio/tests/test_routes.py`, replace `_pass_label_id` and the project/label-related tests (`test_get_project_includes_labels`, `test_put_project_updates_criteria_agent_name_and_labels_atomically`, `test_put_project_rejects_invalid_agent_name`, `test_put_project_returns_409_when_removing_label_in_use`) with:

```python
def _create_queue(client: TestClient, project_id: int, **overrides) -> dict:
    payload = {
        "name": "Agent turns",
        "query": "SELECT trace_id, span_id, start_timestamp, attributes FROM records "
                 "WHERE span_name = 'invoke_agent rx_assistant_agent' ORDER BY start_timestamp DESC",
        "criteria_text": "",
        "sampling_percentage": 100,
        "labels": [{"id": None, "name": "Pass"}, {"id": None, "name": "Fail"}],
        "annotator_ids": [],
    }
    payload.update(overrides)
    return client.post(f"/api/projects/{project_id}/queues", json=payload).json()


def test_get_project_no_longer_includes_labels() -> None:
    client = _app()
    project_id = _project_id(client)

    response = client.get(f"/api/projects/{project_id}")

    assert "labels" not in response.json()


def test_put_project_renames() -> None:
    client = _app()
    project_id = _project_id(client)

    response = client.put(f"/api/projects/{project_id}", json={"name": "renamed"})

    assert response.status_code == 200
    assert response.json()["name"] == "renamed"


def test_list_queues_includes_seeded_default_queue() -> None:
    client = _app()
    project_id = _project_id(client)

    response = client.get(f"/api/projects/{project_id}/queues")

    assert response.status_code == 200
    assert response.json()[0]["name"] == "All rx_assistant interactions"


def test_create_queue_validates_query(monkeypatch) -> None:
    async def fake_validate_columns(read_token, query):
        return None

    monkeypatch.setattr(routes, "validate_query_columns", fake_validate_columns)
    client = _app()
    project_id = _project_id(client)

    response = client.post(
        f"/api/projects/{project_id}/queues",
        json={
            "name": "New queue", "query": "DELETE FROM records", "criteria_text": "",
            "sampling_percentage": 100, "labels": [{"id": None, "name": "Pass"}], "annotator_ids": [],
        },
    )

    assert response.status_code == 400


def test_create_queue_succeeds_with_valid_query(monkeypatch) -> None:
    async def fake_validate_columns(read_token, query):
        return None

    monkeypatch.setattr(routes, "validate_query_columns", fake_validate_columns)
    client = _app()
    project_id = _project_id(client)

    queue = _create_queue(client, project_id)

    assert queue["name"] == "Agent turns"
    assert [l["name"] for l in queue["labels"]] == ["Pass", "Fail"]


def test_get_queue_includes_is_accessible(monkeypatch) -> None:
    monkeypatch.setattr(routes, "validate_query_columns", lambda read_token, query: _noop_coro())
    client = _app()
    project_id = _project_id(client)
    ada = _create_annotator(client, "Ada")
    queue = _create_queue(client, project_id, annotator_ids=[ada["id"]])

    open_view = client.get(f"/api/queues/{queue['id']}")
    ada_view = client.get(f"/api/queues/{queue['id']}?annotator_id={ada['id']}")

    assert open_view.json()["is_accessible"] is False
    assert ada_view.json()["is_accessible"] is True


def test_update_queue_renames() -> None:
    client = _app()
    project_id = _project_id(client)
    queue_id = client.get(f"/api/projects/{project_id}/queues").json()[0]["id"]

    response = client.put(f"/api/queues/{queue_id}", json={"name": "Renamed"})

    assert response.status_code == 200
    assert response.json()["name"] == "Renamed"


def test_delete_queue_removes_it() -> None:
    client = _app()
    project_id = _project_id(client)
    queue_id = client.get(f"/api/projects/{project_id}/queues").json()[0]["id"]

    response = client.delete(f"/api/queues/{queue_id}")

    assert response.status_code == 204
    assert client.get(f"/api/projects/{project_id}/queues").json() == []
```

Add this helper near the top of the test file, used by `test_get_queue_includes_is_accessible`:

```python
async def _noop_coro():
    return None
```

Delete the old `_pass_label_id` helper and every test in the file that references `/api/projects/{id}/interactions` or `/api/projects/{id}/annotations/...` — those move to Task 10 (they'll fail to collect against the new routes until then). Keep the annotator lifecycle tests (`test_annotator_crud_lifecycle`, `test_delete_referenced_annotator_returns_409` — but rewrite the latter's body to use a queue instead of a project for the annotation call, since that endpoint moves in Task 10; simplest is to delete `test_delete_referenced_annotator_returns_409` here too and re-add it in Task 10 once the queue-scoped annotation route exists).

- [ ] **Step 2: Run to verify failure**

Run: `uv run --package annotation-studio pytest apps/annotation-studio/tests/test_routes.py -v`
Expected: FAIL — no `/api/projects/{id}/queues` route yet, `ProjectUpdateRequest` still expects the old shape.

- [ ] **Step 3: Implement**

In `apps/annotation-studio/src/annotation_studio/routes.py`, replace the imports, request models, and project/queue routes:

```python
import sqlite3
from dataclasses import asdict

from anyio import to_thread
from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

from annotation_studio import db
from annotation_studio.logfire_client import validate_query, validate_query_columns
from annotation_studio.logfire_writer import AnnotationWriter
from annotation_studio.settings import AppSettings, SourceSettings

PAGE_SIZE = 20


class LabelPayload(BaseModel):
    id: int | None = None
    name: str


class ProjectUpdateRequest(BaseModel):
    name: str


class QueueCreateRequest(BaseModel):
    name: str
    query: str
    criteria_text: str = ""
    sampling_percentage: int = 100
    labels: list[LabelPayload]
    annotator_ids: list[int] = []


class QueueUpdateRequest(BaseModel):
    name: str | None = None
    query: str | None = None
    criteria_text: str | None = None
    sampling_percentage: int | None = None
    labels: list[LabelPayload] | None = None
    annotator_ids: list[int] | None = None


class AnnotatorRequest(BaseModel):
    name: str


class AnnotationUpdateRequest(BaseModel):
    annotator_id: int
    label_id: int | None = None
    description: str = ""


def _queue_is_accessible(queue: dict, annotator_id: int | None) -> bool:
    if not queue["annotator_ids"]:
        return True
    return annotator_id is not None and annotator_id in queue["annotator_ids"]


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
        return project

    @router.put("/projects/{project_id}")
    async def update_project(project_id: int, payload: ProjectUpdateRequest) -> dict:
        if db.get_project(conn, project_id) is None:
            raise HTTPException(status_code=404, detail="project_not_found")
        try:
            return db.update_project(conn, project_id, payload.name)
        except db.ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.get("/projects/{project_id}/queues")
    async def list_queues(project_id: int, annotator_id: int | None = None) -> list[dict]:
        if db.get_project(conn, project_id) is None:
            raise HTTPException(status_code=404, detail="project_not_found")
        queues = db.list_queues(conn, project_id)
        for queue in queues:
            queue["is_accessible"] = _queue_is_accessible(queue, annotator_id)
        return queues

    @router.post("/projects/{project_id}/queues")
    async def create_queue(project_id: int, payload: QueueCreateRequest) -> dict:
        if db.get_project(conn, project_id) is None:
            raise HTTPException(status_code=404, detail="project_not_found")
        try:
            query = validate_query(payload.query)
            await validate_query_columns(source_settings.read_token, query)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        labels = [db.LabelInput(id=label.id, name=label.name) for label in payload.labels]
        try:
            return db.create_queue(
                conn, project_id, payload.name, query, payload.criteria_text,
                payload.sampling_percentage, labels, payload.annotator_ids,
            )
        except db.ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except db.ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @router.get("/queues/{queue_id}")
    async def get_queue(queue_id: int, annotator_id: int | None = None) -> dict:
        queue = db.get_queue(conn, queue_id)
        if queue is None:
            raise HTTPException(status_code=404, detail="queue_not_found")
        queue["is_accessible"] = _queue_is_accessible(queue, annotator_id)
        return queue

    @router.put("/queues/{queue_id}")
    async def update_queue(queue_id: int, payload: QueueUpdateRequest) -> dict:
        if db.get_queue(conn, queue_id) is None:
            raise HTTPException(status_code=404, detail="queue_not_found")
        query = None
        if payload.query is not None:
            try:
                query = validate_query(payload.query)
                await validate_query_columns(source_settings.read_token, query)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
        labels = (
            [db.LabelInput(id=label.id, name=label.name) for label in payload.labels]
            if payload.labels is not None else None
        )
        try:
            return db.update_queue(
                conn, queue_id, payload.name, query, payload.criteria_text,
                payload.sampling_percentage, labels, payload.annotator_ids,
            )
        except db.ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except db.ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @router.delete("/queues/{queue_id}", status_code=204)
    async def delete_queue(queue_id: int) -> None:
        if db.get_queue(conn, queue_id) is None:
            raise HTTPException(status_code=404, detail="queue_not_found")
        db.delete_queue(conn, queue_id)

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

    app.include_router(router)
```

(The queue-items/refresh/annotation routes are added to this same `router` in Task 10, before `app.include_router(router)` — for now the function ends there.)

In `apps/annotation-studio/src/annotation_studio/main.py`, update the seeding call:

```python
    db.seed_default_project(conn)
    db.seed_default_queue(conn, db.list_projects(conn)[0]["id"], source_settings.top_level_agent_name)
```

replacing the old `db.seed_default_project(conn, source_settings.top_level_agent_name)` line.

- [ ] **Step 4: Run to verify pass**

Run: `uv run --package annotation-studio pytest apps/annotation-studio/tests/test_routes.py -v`
Expected: PASS for every remaining test in the file (the interaction/annotation tests you deleted in Step 1 are gone; they return in Task 10).

- [ ] **Step 5: Commit**

```bash
git add apps/annotation-studio/src/annotation_studio/routes.py apps/annotation-studio/src/annotation_studio/main.py apps/annotation-studio/tests/test_routes.py
git commit -m "annotation-studio: add project and queue CRUD routes"
```

---

## Task 10: Queue items, refresh, and annotation routes

**Files:**
- Modify: `apps/annotation-studio/src/annotation_studio/routes.py`
- Test: `apps/annotation-studio/tests/test_routes.py`

**Interfaces:**
- Consumes: `db.list_queue_items/insert_queue_items/set_queue_last_refreshed/get_annotation/upsert_annotation/mark_writeback_written/mark_writeback_failed` (Tasks 3–4), `logfire_client.fetch_queue_matches/fetch_queue_item_content/sample_included` (Tasks 5–7), `_queue_is_accessible` (Task 9).
- Produces: `POST /api/queues/{id}/refresh`, `GET /api/queues/{id}/items`, `PUT /api/queues/{id}/annotations/{trace_id}/{span_id}`.

- [ ] **Step 1: Write failing tests**

Append to `apps/annotation-studio/tests/test_routes.py`:

```python
from annotation_studio.logfire_client import Interaction


def _seeded_queue_id(client: TestClient, project_id: int) -> int:
    return client.get(f"/api/projects/{project_id}/queues").json()[0]["id"]


def _pass_label_id(client: TestClient, queue_id: int) -> int:
    return next(l["id"] for l in client.get(f"/api/queues/{queue_id}").json()["labels"] if l["name"] == "Pass")


def test_refresh_pulls_new_matches_and_applies_sampling(monkeypatch) -> None:
    async def fake_fetch_matches(read_token, query, min_timestamp, max_timestamp, limit):
        return [
            {"trace_id": "trace-1", "span_id": "span-1", "start_timestamp": "2026-08-28T00:00:00Z"},
            {"trace_id": "trace-2", "span_id": "span-2", "start_timestamp": "2026-08-28T00:01:00Z"},
        ]

    monkeypatch.setattr(routes, "fetch_queue_matches", fake_fetch_matches)
    monkeypatch.setattr(routes, "sample_included", lambda queue_id, trace_id, span_id, pct: trace_id == "trace-1")
    client = _app()
    project_id = _project_id(client)
    queue_id = _seeded_queue_id(client, project_id)

    response = client.post(f"/api/queues/{queue_id}/refresh")

    assert response.status_code == 200
    assert response.json()["new_item_count"] == 1
    assert response.json()["total_item_count"] == 1


def test_refresh_returns_403_when_annotator_not_assigned(monkeypatch) -> None:
    monkeypatch.setattr(
        routes, "fetch_queue_matches", lambda *a, **k: _async_return([])
    )
    client = _app()
    project_id = _project_id(client)
    ada = _create_annotator(client, "Ada")
    outsider = _create_annotator(client, "Outsider")
    queue_id = _seeded_queue_id(client, project_id)
    client.put(f"/api/queues/{queue_id}", json={"annotator_ids": [ada["id"]]})

    response = client.post(f"/api/queues/{queue_id}/refresh?annotator_id={outsider['id']}")

    assert response.status_code == 403


def test_list_items_merges_only_the_requesting_annotators_grade(monkeypatch) -> None:
    async def fake_fetch_matches(read_token, query, min_timestamp, max_timestamp, limit):
        return [{"trace_id": "trace-1", "span_id": "span-1", "start_timestamp": "2026-08-28T00:00:00Z"}]

    async def fake_fetch_content(read_token, items):
        return {
            ("trace-1", "span-1"): Interaction(
                trace_id="trace-1", span_id="span-1", start_timestamp="2026-08-28T00:00:00Z",
                input_text="q", output_text="a", full_conversation=[], trace_url="https://example.test",
            )
        }

    monkeypatch.setattr(routes, "fetch_queue_matches", fake_fetch_matches)
    monkeypatch.setattr(routes, "fetch_queue_item_content", fake_fetch_content)
    client = _app()
    project_id = _project_id(client)
    queue_id = _seeded_queue_id(client, project_id)
    client.post(f"/api/queues/{queue_id}/refresh")
    ada = _create_annotator(client, "Ada")
    grace = _create_annotator(client, "Grace")
    pass_id = _pass_label_id(client, queue_id)
    client.put(
        f"/api/queues/{queue_id}/annotations/trace-1/span-1",
        json={"annotator_id": ada["id"], "label_id": pass_id, "description": "good"},
    )

    ada_page = client.get(f"/api/queues/{queue_id}/items?annotator_id={ada['id']}").json()
    grace_page = client.get(f"/api/queues/{queue_id}/items?annotator_id={grace['id']}").json()

    assert ada_page["items"][0]["annotation"]["label_id"] == pass_id
    assert grace_page["items"][0]["annotation"] is None


def test_list_items_requires_annotator_id() -> None:
    client = _app()
    project_id = _project_id(client)
    queue_id = _seeded_queue_id(client, project_id)

    assert client.get(f"/api/queues/{queue_id}/items").status_code == 400


def test_list_items_returns_403_when_annotator_not_assigned() -> None:
    client = _app()
    project_id = _project_id(client)
    ada = _create_annotator(client, "Ada")
    outsider = _create_annotator(client, "Outsider")
    queue_id = _seeded_queue_id(client, project_id)
    client.put(f"/api/queues/{queue_id}", json={"annotator_ids": [ada["id"]]})

    response = client.get(f"/api/queues/{queue_id}/items?annotator_id={outsider['id']}")

    assert response.status_code == 403


def test_upsert_annotation_creates_and_writes_back() -> None:
    writer = FakeWriter()
    client = _app(writer=writer)
    project_id = _project_id(client)
    queue_id = _seeded_queue_id(client, project_id)
    annotator = _create_annotator(client, "Ada")
    pass_id = _pass_label_id(client, queue_id)

    response = client.put(
        f"/api/queues/{queue_id}/annotations/trace-1/span-1",
        json={"annotator_id": annotator["id"], "label_id": pass_id, "description": "Correct and grounded."},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["writeback_status"] == "written"
    assert len(writer.calls) == 1
    assert writer.calls[0][0]["queue_id"] == queue_id


def test_failed_writeback_keeps_saved_grade() -> None:
    client = _app(writer=FakeWriter(fail=True))
    project_id = _project_id(client)
    queue_id = _seeded_queue_id(client, project_id)
    annotator = _create_annotator(client, "Ada")
    pass_id = _pass_label_id(client, queue_id)

    response = client.put(
        f"/api/queues/{queue_id}/annotations/trace-1/span-1",
        json={"annotator_id": annotator["id"], "label_id": pass_id, "description": "Grounded"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["writeback_status"] == "failed"
    assert "RuntimeError" in body["writeback_error"]


def test_upsert_annotation_rejects_unknown_annotator() -> None:
    client = _app()
    project_id = _project_id(client)
    queue_id = _seeded_queue_id(client, project_id)

    response = client.put(
        f"/api/queues/{queue_id}/annotations/trace-1/span-1",
        json={"annotator_id": 999, "label_id": None, "description": ""},
    )

    assert response.status_code == 400


def test_upsert_annotation_returns_403_when_annotator_not_assigned() -> None:
    client = _app()
    project_id = _project_id(client)
    ada = _create_annotator(client, "Ada")
    outsider = _create_annotator(client, "Outsider")
    queue_id = _seeded_queue_id(client, project_id)
    client.put(f"/api/queues/{queue_id}", json={"annotator_ids": [ada["id"]]})
    pass_id = _pass_label_id(client, queue_id)

    response = client.put(
        f"/api/queues/{queue_id}/annotations/trace-1/span-1",
        json={"annotator_id": outsider["id"], "label_id": pass_id, "description": ""},
    )

    assert response.status_code == 403


def test_delete_referenced_annotator_returns_409() -> None:
    client = _app()
    project_id = _project_id(client)
    queue_id = _seeded_queue_id(client, project_id)
    annotator = _create_annotator(client, "Ada")
    pass_id = _pass_label_id(client, queue_id)
    client.put(
        f"/api/queues/{queue_id}/annotations/trace-1/span-1",
        json={"annotator_id": annotator["id"], "label_id": pass_id, "description": "ok"},
    )

    response = client.delete(f"/api/annotators/{annotator['id']}")

    assert response.status_code == 409
```

Add this helper near the top of the test file (used by `test_refresh_returns_403_when_annotator_not_assigned`):

```python
async def _async_return(value):
    return value
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --package annotation-studio pytest apps/annotation-studio/tests/test_routes.py -v`
Expected: FAIL — `/api/queues/{id}/refresh`, `/items`, and `/annotations/...` routes don't exist yet (404s where 200/403/400 are expected).

- [ ] **Step 3: Implement**

In `apps/annotation-studio/src/annotation_studio/routes.py`, add to the imports:

```python
from datetime import datetime, timedelta, timezone

from annotation_studio.logfire_client import (
    LOOKBACK_DAYS,
    fetch_queue_item_content,
    fetch_queue_matches,
    sample_included,
    validate_query,
    validate_query_columns,
)
```

(replacing the narrower `from annotation_studio.logfire_client import validate_query, validate_query_columns` added in Task 9).

Add a `DatasetCreateRequest` stub is NOT needed yet (Task 12). Insert these routes into `register_routes`, inside the `router = APIRouter(...)` block, before `app.include_router(router)` (after the queue CRUD routes from Task 9):

```python
    @router.post("/queues/{queue_id}/refresh")
    async def refresh_queue(queue_id: int, annotator_id: int | None = None) -> dict:
        queue = db.get_queue(conn, queue_id)
        if queue is None:
            raise HTTPException(status_code=404, detail="queue_not_found")
        if not _queue_is_accessible(queue, annotator_id):
            raise HTTPException(status_code=403, detail="not_assigned_to_queue")

        now = datetime.now(timezone.utc)
        floor = now - timedelta(days=LOOKBACK_DAYS)
        min_timestamp = floor
        if queue["last_refreshed_at"]:
            last = datetime.fromisoformat(queue["last_refreshed_at"])
            min_timestamp = max(floor, last)
        try:
            matches = await fetch_queue_matches(source_settings.read_token, queue["query"], min_timestamp, now, limit=1000)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Logfire query failed: {exc}")

        sampled_in = [
            match for match in matches
            if sample_included(queue_id, match["trace_id"], match["span_id"], queue["sampling_percentage"])
        ]
        new_item_count = db.insert_queue_items(conn, queue_id, sampled_in)
        db.set_queue_last_refreshed(conn, queue_id, now.isoformat())
        total_item_count = len(db.list_queue_items(conn, queue_id, None, 10_000_000)[0])
        return {"new_item_count": new_item_count, "total_item_count": total_item_count}

    @router.get("/queues/{queue_id}/items")
    async def list_items(queue_id: int, annotator_id: int | None = None, cursor: str | None = None) -> dict:
        queue = db.get_queue(conn, queue_id)
        if queue is None:
            raise HTTPException(status_code=404, detail="queue_not_found")
        if annotator_id is None:
            raise HTTPException(status_code=400, detail="annotator_id is required")
        if db.get_annotator(conn, annotator_id) is None:
            raise HTTPException(status_code=400, detail="unknown_annotator_id")
        if not _queue_is_accessible(queue, annotator_id):
            raise HTTPException(status_code=403, detail="not_assigned_to_queue")

        page, next_cursor = db.list_queue_items(conn, queue_id, cursor, PAGE_SIZE)
        content = await fetch_queue_item_content(
            source_settings.read_token, [(item["trace_id"], item["span_id"]) for item in page]
        )

        items = []
        for item in page:
            interaction = content.get((item["trace_id"], item["span_id"]))
            annotation = db.get_annotation(conn, queue_id, item["trace_id"], item["span_id"], annotator_id)
            if interaction is None:
                items.append({
                    "trace_id": item["trace_id"], "span_id": item["span_id"],
                    "start_timestamp": item["start_timestamp"], "unavailable": True,
                    "annotation": annotation,
                })
            else:
                items.append({**asdict(interaction), "unavailable": False, "annotation": annotation})
        return {"items": items, "next_cursor": next_cursor}

    @router.put("/queues/{queue_id}/annotations/{trace_id}/{span_id}")
    async def upsert_annotation(queue_id: int, trace_id: str, span_id: str, payload: AnnotationUpdateRequest) -> dict:
        queue = db.get_queue(conn, queue_id)
        if queue is None:
            raise HTTPException(status_code=404, detail="queue_not_found")
        if not _queue_is_accessible(queue, payload.annotator_id):
            raise HTTPException(status_code=403, detail="not_assigned_to_queue")
        try:
            annotation = db.upsert_annotation(
                conn, queue_id, trace_id, span_id, payload.annotator_id, payload.label_id, payload.description,
            )
        except db.ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        annotator = db.get_annotator(conn, payload.annotator_id)
        label = db.get_label(conn, payload.label_id) if payload.label_id else None
        try:
            await to_thread.run_sync(writer.write, annotation, annotator, label)
        except Exception as exc:
            db.mark_writeback_failed(conn, annotation["id"], annotation["revision"], f"{type(exc).__name__}: {exc}")
        else:
            db.mark_writeback_written(conn, annotation["id"], annotation["revision"])
        return db.get_annotation(conn, queue_id, trace_id, span_id, payload.annotator_id)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --package annotation-studio pytest apps/annotation-studio/tests/test_routes.py -v`
Expected: PASS for the whole file.

- [ ] **Step 5: Commit**

```bash
git add apps/annotation-studio/src/annotation_studio/routes.py apps/annotation-studio/tests/test_routes.py
git commit -m "annotation-studio: add queue refresh, item listing, and annotation routes"
```

---

## Task 11: Dataset export module

**Files:**
- Create: `apps/annotation-studio/src/annotation_studio/logfire_datasets.py`
- Modify: `apps/annotation-studio/pyproject.toml`
- Test: `apps/annotation-studio/tests/test_logfire_datasets.py`

**Interfaces:**
- Consumes: `logfire_client.fetch_queue_item_content` (Task 7).
- Produces: `async def push_queue_dataset(read_token: str, datasets_token: str, name: str, annotations: list[dict], label_lookup: dict[int, str], annotator_lookup: dict[int, str]) -> dict` (returns `{"name": str, "case_count": int, "skipped_count": int}`). `annotations` elements are `db.list_annotations_for_dataset` rows (`trace_id`, `span_id`, `annotator_id`, `label_id`, `description`, ...).

- [ ] **Step 1: Add the dependency and write the failing test**

In `apps/annotation-studio/pyproject.toml`, add `"pydantic-evals"` to `dependencies` (alongside the existing `"logfire"` entry).

Create `apps/annotation-studio/tests/test_logfire_datasets.py`:

```python
from annotation_studio import logfire_datasets
from annotation_studio.logfire_client import Interaction


class FakeContentFetcher:
    def __init__(self, content: dict):
        self.content = content
        self.calls = []

    async def __call__(self, read_token, items):
        self.calls.append((read_token, items))
        return self.content


class FakeDatasetsClient:
    def __init__(self):
        self.pushed = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def push_dataset(self, dataset, **kwargs):
        self.pushed.append(dataset)
        return {"name": dataset.name}


def _annotation(trace_id="t1", span_id="s1", annotator_id=1, label_id=10, description="why") -> dict:
    return {
        "trace_id": trace_id, "span_id": span_id, "annotator_id": annotator_id,
        "label_id": label_id, "description": description,
    }


async def test_push_queue_dataset_builds_one_case_per_annotation(monkeypatch) -> None:
    fetcher = FakeContentFetcher({
        ("t1", "s1"): Interaction(
            trace_id="t1", span_id="s1", start_timestamp="2026-08-28T00:00:00Z",
            input_text="q", output_text="a", full_conversation=[], trace_url="https://example.test",
        ),
    })
    monkeypatch.setattr(logfire_datasets, "fetch_queue_item_content", fetcher)
    fake_client = FakeDatasetsClient()
    monkeypatch.setattr(logfire_datasets, "AsyncLogfireAPIClient", lambda api_key: fake_client)

    result = await logfire_datasets.push_queue_dataset(
        "read-token", "datasets-token", "my-dataset", [_annotation()],
        label_lookup={10: "Pass"}, annotator_lookup={1: "Ada"},
    )

    assert result == {"name": "my-dataset", "case_count": 1, "skipped_count": 0}
    pushed_dataset = fake_client.pushed[0]
    assert pushed_dataset.name == "my-dataset"
    case = pushed_dataset.cases[0]
    assert case.inputs == "q"
    assert case.expected_output == "a"
    assert case.metadata == {
        "label": "Pass", "description": "why", "annotator_name": "Ada", "trace_id": "t1", "span_id": "s1",
    }


async def test_push_queue_dataset_one_case_per_annotator_for_the_same_item(monkeypatch) -> None:
    fetcher = FakeContentFetcher({
        ("t1", "s1"): Interaction(
            trace_id="t1", span_id="s1", start_timestamp="2026-08-28T00:00:00Z",
            input_text="q", output_text="a", full_conversation=[], trace_url="https://example.test",
        ),
    })
    monkeypatch.setattr(logfire_datasets, "fetch_queue_item_content", fetcher)
    fake_client = FakeDatasetsClient()
    monkeypatch.setattr(logfire_datasets, "AsyncLogfireAPIClient", lambda api_key: fake_client)
    annotations = [
        _annotation(annotator_id=1, label_id=10),
        _annotation(annotator_id=2, label_id=20),
    ]

    result = await logfire_datasets.push_queue_dataset(
        "read-token", "datasets-token", "my-dataset", annotations,
        label_lookup={10: "Pass", 20: "Fail"}, annotator_lookup={1: "Ada", 2: "Grace"},
    )

    assert result["case_count"] == 2


async def test_push_queue_dataset_skips_items_whose_trace_aged_out(monkeypatch) -> None:
    fetcher = FakeContentFetcher({})  # nothing resolvable — simulates aged-out traces
    monkeypatch.setattr(logfire_datasets, "fetch_queue_item_content", fetcher)
    fake_client = FakeDatasetsClient()
    monkeypatch.setattr(logfire_datasets, "AsyncLogfireAPIClient", lambda api_key: fake_client)

    result = await logfire_datasets.push_queue_dataset(
        "read-token", "datasets-token", "my-dataset", [_annotation()],
        label_lookup={10: "Pass"}, annotator_lookup={1: "Ada"},
    )

    assert result == {"name": "my-dataset", "case_count": 0, "skipped_count": 1}


async def test_push_queue_dataset_uses_raw_row_when_structured_parse_unavailable(monkeypatch) -> None:
    fetcher = FakeContentFetcher({
        ("t1", "s1"): Interaction(
            trace_id="t1", span_id="s1", start_timestamp="2026-08-28T00:00:00Z",
            input_text="", output_text="", full_conversation=[], trace_url="https://example.test",
            raw_row={"score": 0.9},
        ),
    })
    monkeypatch.setattr(logfire_datasets, "fetch_queue_item_content", fetcher)
    fake_client = FakeDatasetsClient()
    monkeypatch.setattr(logfire_datasets, "AsyncLogfireAPIClient", lambda api_key: fake_client)

    result = await logfire_datasets.push_queue_dataset(
        "read-token", "datasets-token", "my-dataset", [_annotation()],
        label_lookup={10: "Pass"}, annotator_lookup={1: "Ada"},
    )

    assert result["case_count"] == 1
    case = fake_client.pushed[0].cases[0]
    assert case.inputs == {"score": 0.9}
    assert case.expected_output is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --package annotation-studio pytest apps/annotation-studio/tests/test_logfire_datasets.py -v`
Expected: FAIL — `apps/annotation-studio/src/annotation_studio/logfire_datasets.py` doesn't exist (`ModuleNotFoundError`). Also run `uv sync --all-packages` first so `pydantic-evals` is installed.

- [ ] **Step 3: Implement**

Create `apps/annotation-studio/src/annotation_studio/logfire_datasets.py`:

```python
from typing import Any

from logfire.experimental.api_client import AsyncLogfireAPIClient
from pydantic_evals import Case, Dataset

from annotation_studio.logfire_client import fetch_queue_item_content


async def push_queue_dataset(
    read_token: str,
    datasets_token: str,
    name: str,
    annotations: list[dict],
    label_lookup: dict[int, str],
    annotator_lookup: dict[int, str],
) -> dict:
    """Builds one Logfire dataset case per annotation (so an item annotated by two annotators
    produces two cases) and pushes them to Logfire's hosted datasets API. An annotation whose
    source trace/span content can no longer be fetched from Logfire (aged out of the 14-day
    query window, most commonly) is skipped and counted, not treated as a fatal error — the
    caller reports both counts so the export's completeness is visible."""
    pairs = [(a["trace_id"], a["span_id"]) for a in annotations]
    content = await fetch_queue_item_content(read_token, pairs)

    cases: list[Case] = []
    skipped_count = 0
    for annotation in annotations:
        interaction = content.get((annotation["trace_id"], annotation["span_id"]))
        if interaction is None:
            skipped_count += 1
            continue
        inputs: Any = interaction.input_text if interaction.raw_row is None else interaction.raw_row
        expected_output: Any = interaction.output_text if interaction.raw_row is None else None
        if interaction.raw_row is None and not interaction.output_text:
            expected_output = None
        cases.append(
            Case(
                name=f"{annotation['trace_id']}:{annotation['span_id']}:{annotation['annotator_id']}",
                inputs=inputs,
                expected_output=expected_output,
                metadata={
                    "label": label_lookup.get(annotation["label_id"]),
                    "description": annotation["description"],
                    "annotator_name": annotator_lookup.get(annotation["annotator_id"]),
                    "trace_id": annotation["trace_id"],
                    "span_id": annotation["span_id"],
                },
            )
        )

    dataset = Dataset[Any, Any, dict[str, Any]](name=name, cases=cases)
    async with AsyncLogfireAPIClient(api_key=datasets_token) as client:
        await client.push_dataset(dataset)

    return {"name": name, "case_count": len(cases), "skipped_count": skipped_count}
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --package annotation-studio pytest apps/annotation-studio/tests/test_logfire_datasets.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/annotation-studio/src/annotation_studio/logfire_datasets.py apps/annotation-studio/pyproject.toml apps/annotation-studio/tests/test_logfire_datasets.py uv.lock
git commit -m "annotation-studio: add Logfire hosted-dataset export from annotated queue items"
```

---

## Task 12: Dataset export route, settings, and wiring

**Files:**
- Modify: `apps/annotation-studio/src/annotation_studio/routes.py`, `apps/annotation-studio/src/annotation_studio/settings.py`, `apps/annotation-studio/.env.example`, `apps/annotation-studio/tests/conftest.py`
- Test: `apps/annotation-studio/tests/test_routes.py`, `apps/annotation-studio/tests/test_settings.py`

**Interfaces:**
- Consumes: `logfire_datasets.push_queue_dataset` (Task 11), `db.list_annotations_for_dataset` (Task 4), `SourceSettings.datasets_token`.
- Produces: `POST /api/queues/{id}/datasets`, `SourceSettings.datasets_token: str`.

- [ ] **Step 1: Write failing tests**

In `apps/annotation-studio/tests/conftest.py`, add a line next to the other forced env vars:

```python
os.environ["RX_ASSISTANT_LOGFIRE_DATASETS_TOKEN"] = "test-datasets-token"
```

Check `apps/annotation-studio/tests/test_settings.py` for its existing `SourceSettings` test and add an assertion for `datasets_token` there (read the file first to match its exact style before editing — it's small, mirroring the `read_token`/`write_token` assertions already in it).

Append to `apps/annotation-studio/tests/test_routes.py`:

```python
def test_create_dataset_pushes_annotated_items(monkeypatch) -> None:
    async def fake_push(read_token, datasets_token, name, annotations, label_lookup, annotator_lookup):
        return {"name": name, "case_count": len(annotations), "skipped_count": 0}

    monkeypatch.setattr(routes, "push_queue_dataset", fake_push)
    client = _app()
    project_id = _project_id(client)
    queue_id = _seeded_queue_id(client, project_id)
    annotator = _create_annotator(client, "Ada")
    pass_id = _pass_label_id(client, queue_id)
    client.put(
        f"/api/queues/{queue_id}/annotations/trace-1/span-1",
        json={"annotator_id": annotator["id"], "label_id": pass_id, "description": "good"},
    )

    response = client.post(f"/api/queues/{queue_id}/datasets", json={"name": "eval-set"})

    assert response.status_code == 200
    assert response.json() == {"name": "eval-set", "case_count": 1, "skipped_count": 0}


def test_create_dataset_filters_by_label(monkeypatch) -> None:
    captured = {}

    async def fake_push(read_token, datasets_token, name, annotations, label_lookup, annotator_lookup):
        captured["annotations"] = annotations
        return {"name": name, "case_count": len(annotations), "skipped_count": 0}

    monkeypatch.setattr(routes, "push_queue_dataset", fake_push)
    client = _app()
    project_id = _project_id(client)
    queue_id = _seeded_queue_id(client, project_id)
    annotator = _create_annotator(client, "Ada")
    pass_id = _pass_label_id(client, queue_id)
    fail_id = next(l["id"] for l in client.get(f"/api/queues/{queue_id}").json()["labels"] if l["name"] == "Fail")
    client.put(f"/api/queues/{queue_id}/annotations/trace-1/span-1", json={"annotator_id": annotator["id"], "label_id": pass_id, "description": "good"})
    client.put(f"/api/queues/{queue_id}/annotations/trace-2/span-2", json={"annotator_id": annotator["id"], "label_id": fail_id, "description": "bad"})

    client.post(f"/api/queues/{queue_id}/datasets", json={"name": "eval-set", "label_id": pass_id})

    assert len(captured["annotations"]) == 1


def test_create_dataset_returns_404_for_unknown_queue() -> None:
    client = _app()

    response = client.post("/api/queues/999/datasets", json={"name": "x"})

    assert response.status_code == 404
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --package annotation-studio pytest apps/annotation-studio/tests/test_routes.py apps/annotation-studio/tests/test_settings.py -v`
Expected: FAIL — `datasets_token` missing from `SourceSettings`, `/api/queues/{id}/datasets` route doesn't exist.

- [ ] **Step 3: Implement**

In `apps/annotation-studio/src/annotation_studio/settings.py`, add to `SourceSettings`:

```python
    datasets_token: str = Field(validation_alias="RX_ASSISTANT_LOGFIRE_DATASETS_TOKEN")
```

In `apps/annotation-studio/src/annotation_studio/routes.py`, add to the imports:

```python
from annotation_studio.logfire_datasets import push_queue_dataset
```

Add the request model near the other `*Request` classes:

```python
class DatasetCreateRequest(BaseModel):
    name: str
    label_id: int | None = None
```

Add the route inside `register_routes`, alongside the other queue routes:

```python
    @router.post("/queues/{queue_id}/datasets")
    async def create_dataset(queue_id: int, payload: DatasetCreateRequest) -> dict:
        queue = db.get_queue(conn, queue_id)
        if queue is None:
            raise HTTPException(status_code=404, detail="queue_not_found")
        annotations = db.list_annotations_for_dataset(conn, queue_id, payload.label_id)
        label_lookup = {label["id"]: label["name"] for label in queue["labels"]}
        annotator_lookup = {a["id"]: a["name"] for a in db.list_annotators(conn)}
        return await push_queue_dataset(
            source_settings.read_token, source_settings.datasets_token, payload.name,
            annotations, label_lookup, annotator_lookup,
        )
```

In `apps/annotation-studio/.env.example`, add after `RX_ASSISTANT_LOGFIRE_WRITE_TOKEN`:

```
# Publishes queue exports as Logfire hosted datasets (project:write_datasets scope),
# minted separately from the read/write tokens above.
RX_ASSISTANT_LOGFIRE_DATASETS_TOKEN=
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --package annotation-studio pytest apps/annotation-studio/tests/ -v`
Expected: PASS for the entire backend test suite.

- [ ] **Step 5: Commit**

```bash
git add apps/annotation-studio/src/annotation_studio/routes.py apps/annotation-studio/src/annotation_studio/settings.py apps/annotation-studio/.env.example apps/annotation-studio/tests/conftest.py apps/annotation-studio/tests/test_routes.py apps/annotation-studio/tests/test_settings.py
git commit -m "annotation-studio: wire dataset export route and settings token"
```

---

## Task 13: Frontend types and API client

**Files:**
- Modify: `apps/annotation-studio/frontend/src/types.ts`, `apps/annotation-studio/frontend/src/api.ts`

**Interfaces:**
- Produces: TypeScript types `Queue`, `QueueSummary`, `QueueItem`, `QueueItemsPage`; API functions `listQueues`, `getQueue`, `createQueue`, `updateQueue`, `deleteQueue`, `refreshQueue`, `listQueueItems`, `upsertQueueAnnotation`, `createDataset`; updated `updateProject`.

- [ ] **Step 1–4 combined (no backend test cycle applies to type/API-client files; verify via the TypeScript compiler instead)**

Replace `apps/annotation-studio/frontend/src/types.ts` in full:

```typescript
export interface Label {
  id: number;
  name: string;
  sort_order: number;
}

export interface ProjectSummary {
  id: number;
  name: string;
  created_at: string;
  updated_at: string;
}

export type Project = ProjectSummary;

export interface QueueSummary {
  id: number;
  project_id: number;
  name: string;
  query: string;
  criteria_text: string;
  sampling_percentage: number;
  last_refreshed_at: string | null;
  labels: Label[];
  annotator_ids: number[];
  item_count: number;
  created_at: string;
  updated_at: string;
}

export interface Queue extends Omit<QueueSummary, "item_count"> {
  is_accessible: boolean;
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

export interface QueueItem {
  trace_id: string;
  span_id: string;
  start_timestamp: string;
  input_text?: string;
  output_text?: string;
  full_conversation?: Message[];
  trace_url?: string;
  raw_row: Record<string, unknown> | null;
  unavailable: boolean;
  annotation: Annotation | null;
}

export interface QueueItemsPage {
  items: QueueItem[];
  next_cursor: string | null;
}

export interface DatasetResult {
  name: string;
  case_count: number;
  skipped_count: number;
}
```

Replace `apps/annotation-studio/frontend/src/api.ts` in full:

```typescript
import type {
  Annotation,
  Annotator,
  DatasetResult,
  Label,
  Project,
  ProjectSummary,
  Queue,
  QueueItemsPage,
  QueueSummary,
} from "./types";

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

export function updateProject(projectId: number, name: string): Promise<Project> {
  return request<Project>(`/api/projects/${projectId}`, { method: "PUT", body: JSON.stringify({ name }) });
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

export interface QueueDraft {
  name: string;
  query: string;
  criteria_text: string;
  sampling_percentage: number;
  labels: { id: number | null; name: string }[];
  annotator_ids: number[];
}

export function listQueues(projectId: number, annotatorId: number | null): Promise<QueueSummary[]> {
  const params = new URLSearchParams();
  if (annotatorId !== null) params.set("annotator_id", String(annotatorId));
  const qs = params.toString();
  return request<QueueSummary[]>(`/api/projects/${projectId}/queues${qs ? `?${qs}` : ""}`);
}

export function createQueue(projectId: number, draft: QueueDraft): Promise<Queue> {
  return request<Queue>(`/api/projects/${projectId}/queues`, { method: "POST", body: JSON.stringify(draft) });
}

export function getQueue(queueId: number, annotatorId: number | null): Promise<Queue> {
  const params = new URLSearchParams();
  if (annotatorId !== null) params.set("annotator_id", String(annotatorId));
  const qs = params.toString();
  return request<Queue>(`/api/queues/${queueId}${qs ? `?${qs}` : ""}`);
}

export function updateQueue(queueId: number, draft: Partial<QueueDraft>): Promise<Queue> {
  return request<Queue>(`/api/queues/${queueId}`, { method: "PUT", body: JSON.stringify(draft) });
}

export function deleteQueue(queueId: number): Promise<void> {
  return request<void>(`/api/queues/${queueId}`, { method: "DELETE" });
}

export function refreshQueue(queueId: number, annotatorId: number | null): Promise<{ new_item_count: number; total_item_count: number }> {
  const params = new URLSearchParams();
  if (annotatorId !== null) params.set("annotator_id", String(annotatorId));
  const qs = params.toString();
  return request(`/api/queues/${queueId}/refresh${qs ? `?${qs}` : ""}`, { method: "POST" });
}

export function listQueueItems(queueId: number, annotatorId: number, cursor: string | null): Promise<QueueItemsPage> {
  const params = new URLSearchParams({ annotator_id: String(annotatorId) });
  if (cursor) params.set("cursor", cursor);
  return request<QueueItemsPage>(`/api/queues/${queueId}/items?${params.toString()}`);
}

export function upsertQueueAnnotation(
  queueId: number,
  traceId: string,
  spanId: string,
  payload: { annotator_id: number; label_id: number | null; description: string },
): Promise<Annotation> {
  return request<Annotation>(`/api/queues/${queueId}/annotations/${traceId}/${spanId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function createDataset(queueId: number, name: string, labelId: number | null): Promise<DatasetResult> {
  return request<DatasetResult>(`/api/queues/${queueId}/datasets`, {
    method: "POST",
    body: JSON.stringify({ name, label_id: labelId }),
  });
}
```

`Label` stays imported in `types.ts` implicitly via `QueueSummary`/`Queue`; no unused-import issue since it's used there.

- [ ] **Step 5: Verify and commit**

Run: `cd apps/annotation-studio/frontend && npx tsc --noEmit`
Expected: errors in every file that still references the old `Interaction`/`InteractionsPage` types or the old `updateProject(id, {criteria_text, ...})` signature — that's expected; those files are fixed in Tasks 14–16. Confirm the errors are confined to `pages/ProjectDetail.tsx`, `components/ProjectEditor.tsx`, `components/InteractionRow.tsx`, `pages/ProjectList.tsx` (the `top_level_agent_name` reference) and nowhere else, then commit:

```bash
git add apps/annotation-studio/frontend/src/types.ts apps/annotation-studio/frontend/src/api.ts
git commit -m "annotation-studio: add queue types and API client functions"
```

---

## Task 14: Project list and queue list pages

**Files:**
- Modify: `apps/annotation-studio/frontend/src/pages/ProjectList.tsx`, `apps/annotation-studio/frontend/src/pages/ProjectDetail.tsx`, `apps/annotation-studio/frontend/src/index.css`
- Delete: `apps/annotation-studio/frontend/src/components/ProjectEditor.tsx` (its label-editing UI moves into the new `QueueForm` in Task 15)

**Interfaces:**
- Consumes: `listProjects`, `getProject`, `listQueues`, `updateProject`, `deleteQueue` (Task 13), `useAnnotator` (unchanged).
- Produces: `ProjectList` (drop the `top_level_agent_name` reference), `ProjectDetail` rewritten as a queue list.

- [ ] **Step 1: Fix `ProjectList.tsx`**

In `apps/annotation-studio/frontend/src/pages/ProjectList.tsx`, remove the line `<p className="project-card-meta">Source agent: {project.top_level_agent_name}</p>` (project no longer has that field) — the card keeps its icon/title/arrow, just without the meta line.

- [ ] **Step 2: Rewrite `ProjectDetail.tsx` as a queue list**

Replace `apps/annotation-studio/frontend/src/pages/ProjectDetail.tsx` in full:

```tsx
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { deleteQueue, getProject, listQueues } from "../api";
import { useAnnotator } from "../annotator";
import { AppHeader } from "../components/AppHeader";
import type { Project, QueueSummary } from "../types";

export function ProjectDetail() {
  const { id } = useParams<{ id: string }>();
  const projectId = Number(id);
  const { selectedId } = useAnnotator();

  const [project, setProject] = useState<Project | null>(null);
  const [queues, setQueues] = useState<QueueSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    getProject(projectId)
      .then(setProject)
      .catch((err: unknown) => setError(String(err)));
    listQueues(projectId, selectedId)
      .then(setQueues)
      .catch((err: unknown) => setError(String(err)));
  }, [projectId, selectedId]);

  useEffect(load, [load]);

  const handleDelete = async (queueId: number) => {
    if (!confirm("Delete this queue and all its annotations? This cannot be undone.")) return;
    await deleteQueue(queueId);
    load();
  };

  return (
    <div className="page page-wide">
      <AppHeader />
      <main className="page-body">
        {error && <p className="error-banner">{error}</p>}
        {project && (
          <div className="page-heading page-heading-row">
            <h1>{project.name}</h1>
            <Link className="btn btn-primary" to={`/projects/${projectId}/queues/new`}>
              + New queue
            </Link>
          </div>
        )}
        {queues !== null && (
          <div className="queue-list">
            {queues.map((queue) => (
              <div key={queue.id} className="card queue-card">
                <Link to={`/queues/${queue.id}`} className="queue-card-main">
                  <h2>{queue.name}</h2>
                  <p className="queue-card-meta">
                    {queue.item_count} item{queue.item_count === 1 ? "" : "s"} · {queue.sampling_percentage}% sampled ·{" "}
                    {queue.annotator_ids.length === 0 ? "open to all annotators" : `${queue.annotator_ids.length} assigned annotator(s)`}
                  </p>
                </Link>
                <div className="queue-card-actions">
                  <Link className="btn btn-secondary" to={`/queues/${queue.id}/edit`}>
                    Edit
                  </Link>
                  <button className="btn btn-danger" onClick={() => handleDelete(queue.id)}>
                    Delete
                  </button>
                </div>
              </div>
            ))}
            {queues.length === 0 && <p className="loading-text">No queues yet — create one to get started.</p>}
          </div>
        )}
      </main>
    </div>
  );
}
```

Delete `apps/annotation-studio/frontend/src/components/ProjectEditor.tsx` (`rm apps/annotation-studio/frontend/src/components/ProjectEditor.tsx`) — its label-list editing JSX is carried into `QueueForm.tsx` in Task 15 rather than reused as an import, since the new component's surrounding fields (query, sampling, annotators) differ enough that a shared component isn't a clean fit; duplicating the ~20-line label-chip-list block is preferable to threading it through a generic wrapper for one caller.

Add to `apps/annotation-studio/frontend/src/index.css` (near the existing `.project-card`/`.card` rules — open the file, find the `.project-card` block, and add these rules after it):

```css
.page-heading-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.queue-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.queue-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.queue-card-main {
  flex: 1;
  color: inherit;
  text-decoration: none;
}

.queue-card-meta {
  color: var(--color-text-muted);
  font-size: 0.9em;
  margin-top: 4px;
}

.queue-card-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.btn-danger {
  background: var(--color-danger-soft);
  color: var(--color-danger);
}
```

- [ ] **Step 3: Verify**

Run: `cd apps/annotation-studio/frontend && npx tsc --noEmit`
Expected: no errors from `ProjectList.tsx` or `ProjectDetail.tsx` anymore; remaining errors are confined to `components/InteractionRow.tsx` (fixed in Task 16) and the not-yet-created `pages/QueueForm.tsx`/`pages/QueueDetail.tsx` routes referenced nowhere yet.

Start the app locally (`cd apps/annotation-studio/frontend && npm run dev` in one terminal, `uv run --package annotation-studio uvicorn annotation_studio.main:app --reload` in another) and confirm in a browser that `/` lists the project, and `/projects/1` shows the seeded "All rx_assistant interactions" queue card with a working (if 404ing, until Task 15/16 land the routes) Edit link.

- [ ] **Step 4: Commit**

```bash
git add apps/annotation-studio/frontend/src/pages/ProjectList.tsx apps/annotation-studio/frontend/src/pages/ProjectDetail.tsx apps/annotation-studio/frontend/src/index.css
git rm apps/annotation-studio/frontend/src/components/ProjectEditor.tsx
git commit -m "annotation-studio: project detail becomes a queue list"
```

---

## Task 15: Queue form (create/edit)

**Files:**
- Create: `apps/annotation-studio/frontend/src/pages/QueueForm.tsx`
- Modify: `apps/annotation-studio/frontend/src/index.css`

**Interfaces:**
- Consumes: `createQueue`, `updateQueue`, `getQueue`, `listAnnotators` (Task 13), `useAnnotator`.
- Produces: `QueueForm` component used for both `/projects/:id/queues/new` and `/queues/:id/edit` (Task 17 wires the routes).

- [ ] **Step 1: Create the component**

Create `apps/annotation-studio/frontend/src/pages/QueueForm.tsx`:

```tsx
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { createQueue, getQueue, listAnnotators, updateQueue } from "../api";
import { AppHeader } from "../components/AppHeader";
import type { Annotator, Label } from "../types";

interface LabelDraft {
  id: number | null;
  name: string;
}

const QUERY_HELPERS: { label: string; snippet: (agentName: string) => string }[] = [
  {
    label: "Agent turn input/output",
    snippet: (agentName) =>
      `SELECT trace_id, span_id, start_timestamp, duration, attributes FROM records\nWHERE span_name = 'invoke_agent ${agentName || "your_agent_name"}'\nORDER BY start_timestamp DESC`,
  },
  {
    label: "Tool calls",
    snippet: () =>
      "SELECT trace_id, span_id, start_timestamp, duration, attributes FROM records\nWHERE span_name LIKE 'execute_tool %'\nORDER BY start_timestamp DESC",
  },
  {
    label: "Evaluation results (starting point — confirm against real data)",
    snippet: () =>
      "SELECT trace_id, span_id, start_timestamp, duration, attributes FROM records\nWHERE span_name LIKE '%eval%'\nORDER BY start_timestamp DESC",
  },
];

export function QueueForm() {
  const { id: projectIdParam, queueId: queueIdParam } = useParams<{ id?: string; queueId?: string }>();
  const navigate = useNavigate();
  const isEdit = queueIdParam !== undefined;

  const [projectId, setProjectId] = useState<number | null>(projectIdParam ? Number(projectIdParam) : null);
  const [name, setName] = useState("");
  const [query, setQuery] = useState("");
  const [criteriaText, setCriteriaText] = useState("");
  const [samplingPercentage, setSamplingPercentage] = useState(100);
  const [labels, setLabels] = useState<LabelDraft[]>([{ id: null, name: "Pass" }, { id: null, name: "Fail" }]);
  const [annotators, setAnnotators] = useState<Annotator[]>([]);
  const [assignedIds, setAssignedIds] = useState<number[]>([]);
  const [exploreBase, setExploreBase] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listAnnotators().then(setAnnotators);
  }, []);

  useEffect(() => {
    if (!isEdit || !queueIdParam) return;
    getQueue(Number(queueIdParam), null).then((queue) => {
      setProjectId(queue.project_id);
      setName(queue.name);
      setQuery(queue.query);
      setCriteriaText(queue.criteria_text);
      setSamplingPercentage(queue.sampling_percentage);
      setLabels(queue.labels.map((l: Label) => ({ id: l.id, name: l.name })));
      setAssignedIds(queue.annotator_ids);
    });
  }, [isEdit, queueIdParam]);

  const updateLabelName = (index: number, value: string) =>
    setLabels((prev) => prev.map((l, i) => (i === index ? { ...l, name: value } : l)));
  const removeLabel = (index: number) => setLabels((prev) => prev.filter((_, i) => i !== index));
  const addLabel = () => setLabels((prev) => [...prev, { id: null, name: "New label" }]);

  const toggleAnnotator = (annotatorId: number) =>
    setAssignedIds((prev) =>
      prev.includes(annotatorId) ? prev.filter((id) => id !== annotatorId) : [...prev, annotatorId],
    );

  const copyQuery = () => {
    navigator.clipboard?.writeText(query).catch(() => undefined);
  };

  const handleSave = async () => {
    setError(null);
    const trimmedLabels = labels.map((l) => ({ ...l, name: l.name.trim() }));
    if (trimmedLabels.some((l) => l.name.length === 0)) {
      setError("Label name cannot be empty");
      return;
    }
    setSaving(true);
    try {
      const draft = {
        name, query, criteria_text: criteriaText, sampling_percentage: samplingPercentage,
        labels: trimmedLabels, annotator_ids: assignedIds,
      };
      const saved = isEdit ? await updateQueue(Number(queueIdParam), draft) : await createQueue(projectId!, draft);
      navigate(`/queues/${saved.id}`);
    } catch (err) {
      setError(String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="page page-wide">
      <AppHeader />
      <main className="page-body">
        <h1>{isEdit ? "Edit queue" : "New queue"}</h1>

        <section className="card queue-form">
          <div className="field">
            <label htmlFor="queue-name">Name</label>
            <input id="queue-name" value={name} onChange={(e) => setName(e.target.value)} />
          </div>

          <div className="field">
            <label htmlFor="queue-query">Logfire SQL query</label>
            <p className="field-hint">
              A SELECT against Logfire's `records` table. Must return at least trace_id, span_id, start_timestamp.
            </p>
            <div className="query-helpers">
              {QUERY_HELPERS.map((helper) => (
                <button
                  key={helper.label}
                  type="button"
                  className="btn btn-dashed"
                  onClick={() => setQuery(helper.snippet(""))}
                >
                  {helper.label}
                </button>
              ))}
            </div>
            <textarea
              id="queue-query"
              className="query-textarea"
              rows={6}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <div className="query-actions">
              <button type="button" className="btn-link" onClick={copyQuery}>
                Copy query
              </button>
              {exploreBase && (
                <a
                  className="btn-link"
                  href={`${exploreBase}?q=${encodeURIComponent(query)}`}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Open in Logfire Explore ↗
                </a>
              )}
            </div>
          </div>

          <div className="field">
            <label htmlFor="queue-criteria">Grading criteria</label>
            <textarea id="queue-criteria" rows={6} value={criteriaText} onChange={(e) => setCriteriaText(e.target.value)} />
          </div>

          <div className="field">
            <label>Labels</label>
            <div className="label-chip-list">
              {labels.map((label, index) => (
                <div key={label.id ?? `new-${index}`} className="label-chip-row">
                  <input className="label-chip-input" value={label.name} onChange={(e) => updateLabelName(index, e.target.value)} />
                  <button className="btn-icon btn-icon-danger" onClick={() => removeLabel(index)} aria-label="Remove label">
                    ✕
                  </button>
                </div>
              ))}
            </div>
            <button className="btn btn-dashed btn-dashed-spaced" onClick={addLabel}>
              + Add label
            </button>
          </div>

          <div className="field">
            <label htmlFor="queue-sampling">Sampling percentage</label>
            <p className="field-hint">Of newly discovered matches, what percentage gets added to the queue.</p>
            <input
              id="queue-sampling"
              type="number"
              min={1}
              max={100}
              value={samplingPercentage}
              onChange={(e) => setSamplingPercentage(Number(e.target.value))}
            />
          </div>

          <div className="field">
            <label>Assigned annotators</label>
            <p className="field-hint">Leave empty to make this queue open to every annotator.</p>
            <div className="annotator-checkbox-list">
              {annotators.map((annotator) => (
                <label key={annotator.id} className="annotator-checkbox">
                  <input
                    type="checkbox"
                    checked={assignedIds.includes(annotator.id)}
                    onChange={() => toggleAnnotator(annotator.id)}
                  />
                  {annotator.name}
                </label>
              ))}
              {annotators.length === 0 && <p className="loading-text">No annotator profiles yet.</p>}
            </div>
          </div>

          <div className="card-footer">
            <button className="btn btn-primary" onClick={handleSave} disabled={saving || !name || !query}>
              {saving ? "Saving…" : "Save queue"}
            </button>
            {error && <p className="error-inline">{error}</p>}
          </div>
        </section>
      </main>
    </div>
  );
}
```

`exploreBase` is left `null` (never set) in this task — Task 16 shows the pattern for deriving it from a queue's trace link once one exists (a queue with no items yet has no known trace URL to derive the org/project path from); the create/edit form simply omits the Explore link until the queue has been saved and viewed on its detail page, where Task 16 adds it. Remove `exploreBase`/`setExploreBase` from this file since they're otherwise unused and would fail the TypeScript build (`noUnusedLocals` — check `tsconfig.json`; if it's not enabled, leaving the dead state is still poor practice) — delete the `useState<string | null>(null)` line and the `{exploreBase && (...)}` block, leaving just "Copy query" in this task. The Explore link appears in Task 16 instead, where a real base URL is available from a fetched queue item's `trace_url`.

- [ ] **Step 2: Verify**

Run: `cd apps/annotation-studio/frontend && npx tsc --noEmit`
Expected: no errors from `QueueForm.tsx` itself (it isn't routed yet, so this only checks the file compiles standalone — `npx tsc --noEmit` type-checks the whole project regardless of routing).

- [ ] **Step 3: Commit**

```bash
git add apps/annotation-studio/frontend/src/pages/QueueForm.tsx
git commit -m "annotation-studio: add queue create/edit form with query helpers"
```

---

## Task 16: Queue detail page (review UI, refresh, dataset export)

**Files:**
- Create: `apps/annotation-studio/frontend/src/pages/QueueDetail.tsx`
- Modify: `apps/annotation-studio/frontend/src/components/InteractionRow.tsx` (rename to `QueueItemRow.tsx`), `apps/annotation-studio/frontend/src/index.css`
- Delete: old `apps/annotation-studio/frontend/src/components/InteractionRow.tsx` content (replaced, see below)

**Interfaces:**
- Consumes: `listQueueItems`, `upsertQueueAnnotation`, `refreshQueue`, `createDataset`, `getQueue` (Task 13), `useAnnotator`.
- Produces: `QueueDetail` page, `QueueItemRow` component.

- [ ] **Step 1: Rename and generalize `InteractionRow.tsx` to `QueueItemRow.tsx`**

```bash
git mv apps/annotation-studio/frontend/src/components/InteractionRow.tsx apps/annotation-studio/frontend/src/components/QueueItemRow.tsx
```

Replace its contents in full:

```tsx
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";

import { upsertQueueAnnotation } from "../api";
import type { Label, Message, MessagePart, QueueItem } from "../types";

interface Props {
  queueId: number;
  annotatorId: number;
  item: QueueItem;
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
    <div key={key} className={`transcript-message transcript-message-${message.role}`}>
      <span className="transcript-role">{message.role}</span>
      <div className="transcript-parts">{message.parts.map((part, partIndex) => renderPart(part, partIndex))}</div>
    </div>
  );
}

export function QueueItemRow({ queueId, annotatorId, item, labels }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [showFullConversation, setShowFullConversation] = useState(false);
  const [labelId, setLabelId] = useState<number | null>(item.annotation?.label_id ?? null);
  const [description, setDescription] = useState(item.annotation?.description ?? "");
  const [saved, setSaved] = useState(item.annotation);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    setLabelId(item.annotation?.label_id ?? null);
    setDescription(item.annotation?.description ?? "");
    setSaved(item.annotation);
  }, [item, annotatorId]);

  const currentLabelName = labels.find((l) => l.id === saved?.label_id)?.name ?? "Ungraded";
  const isGraded = saved?.label_id != null;
  const hasStructuredContent = item.raw_row == null;

  const handleSaveAnnotation = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      const result = await upsertQueueAnnotation(queueId, item.trace_id, item.span_id, {
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
    <div className={`card interaction-row${expanded ? " interaction-row-expanded" : ""}`}>
      <button className="interaction-summary" onClick={() => setExpanded((v) => !v)}>
        <span className={`chevron${expanded ? " chevron-open" : ""}`} aria-hidden="true">
          ▸
        </span>
        <span className="timestamp">{new Date(item.start_timestamp).toLocaleString()}</span>
        <span className="preview">
          {item.unavailable ? "(trace no longer available)" : (item.input_text ?? "").slice(0, 120)}
        </span>
        <span className={`badge${isGraded ? " badge-accent" : " badge-neutral"}`}>{currentLabelName}</span>
      </button>

      {expanded && (
        <div className="interaction-detail">
          {item.unavailable ? (
            <div className="content-block content-block-warning">
              <h4>Trace no longer available</h4>
              <p>This item's trace has aged out of Logfire's 14-day query window and can't be displayed.</p>
            </div>
          ) : !hasStructuredContent ? (
            <div className="content-block content-block-warning">
              <h4>Raw row (no recognizable input/output shape)</h4>
              <pre>{JSON.stringify(item.raw_row, null, 2)}</pre>
            </div>
          ) : (
            <>
              <div className="content-grid">
                <div className="content-block">
                  <h4>Input</h4>
                  <div className="markdown-body">
                    <ReactMarkdown>{item.input_text ?? ""}</ReactMarkdown>
                  </div>
                </div>
                <div className="content-block">
                  <h4>Output</h4>
                  <div className="markdown-body">
                    <ReactMarkdown>{item.output_text ?? ""}</ReactMarkdown>
                  </div>
                </div>
              </div>

              <button className="btn-link" onClick={() => setShowFullConversation((v) => !v)}>
                {showFullConversation ? "Hide full conversation" : "View full conversation"}
              </button>
              {showFullConversation && (
                <div className="full-conversation">
                  {(item.full_conversation ?? []).map((message, index) => renderMessage(message, index))}
                </div>
              )}
            </>
          )}

          <div className="grading-panel">
            <h4>Grade</h4>
            <div className="label-picker">
              {labels.map((label) => (
                <button
                  key={label.id}
                  className={`chip${labelId === label.id ? " chip-selected" : ""}`}
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
            <div className="grading-panel-footer">
              <button className="btn btn-primary" onClick={handleSaveAnnotation} disabled={saving}>
                {saving ? "Saving…" : "Save annotation"}
              </button>
              {item.trace_url && (
                <a className="btn-link trace-link" href={item.trace_url} target="_blank" rel="noopener noreferrer">
                  Open trace in Logfire ↗
                </a>
              )}
            </div>
            {saveError && <p className="error-inline">{saveError}</p>}
            {saved?.writeback_status === "failed" && (
              <p className="status-message status-message-warning">
                ⚠ Grade saved locally, but Logfire write-back failed: {saved.writeback_error}
              </p>
            )}
            {saved?.writeback_status === "written" && (
              <p className="status-message status-message-success">✓ Written to Logfire</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Create `QueueDetail.tsx`**

Create `apps/annotation-studio/frontend/src/pages/QueueDetail.tsx`:

```tsx
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, Navigate, useParams } from "react-router-dom";

import { createDataset, getQueue, listQueueItems, refreshQueue } from "../api";
import { useAnnotator } from "../annotator";
import { AppHeader } from "../components/AppHeader";
import { QueueItemRow } from "../components/QueueItemRow";
import type { Queue, QueueItem } from "../types";

export function QueueDetail() {
  const { queueId: queueIdParam } = useParams<{ queueId: string }>();
  const queueId = Number(queueIdParam);
  const { selectedId } = useAnnotator();

  const [queue, setQueue] = useState<Queue | null>(null);
  const [items, setItems] = useState<QueueItem[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshMessage, setRefreshMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showDatasetForm, setShowDatasetForm] = useState(false);
  const [datasetName, setDatasetName] = useState("");
  const [datasetLabelId, setDatasetLabelId] = useState<number | "">("");
  const [datasetResult, setDatasetResult] = useState<string | null>(null);
  const [datasetSaving, setDatasetSaving] = useState(false);

  const activeAnnotatorIdRef = useRef(selectedId);
  useEffect(() => {
    activeAnnotatorIdRef.current = selectedId;
  }, [selectedId]);

  const loadQueue = useCallback(() => {
    getQueue(queueId, selectedId)
      .then(setQueue)
      .catch((err: unknown) => setError(String(err)));
  }, [queueId, selectedId]);

  const loadItems = useCallback(
    (cursor: string | null) => {
      if (selectedId === null) return;
      const firedForAnnotatorId = selectedId;
      setLoading(true);
      listQueueItems(queueId, selectedId, cursor)
        .then((page) => {
          if (activeAnnotatorIdRef.current !== firedForAnnotatorId) return;
          setItems((prev) => (cursor ? [...prev, ...page.items] : page.items));
          setNextCursor(page.next_cursor);
        })
        .catch((err: unknown) => setError(String(err)))
        .finally(() => {
          if (activeAnnotatorIdRef.current === firedForAnnotatorId) setLoading(false);
        });
    },
    [queueId, selectedId],
  );

  useEffect(loadQueue, [loadQueue]);
  useEffect(() => {
    setItems([]);
    setNextCursor(null);
    loadItems(null);
  }, [queueId, selectedId]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleRefresh = async () => {
    setRefreshing(true);
    setRefreshMessage(null);
    try {
      const result = await refreshQueue(queueId, selectedId);
      setRefreshMessage(`Pulled ${result.new_item_count} new item(s) — ${result.total_item_count} total.`);
      setItems([]);
      setNextCursor(null);
      loadItems(null);
    } catch (err) {
      setError(String(err));
    } finally {
      setRefreshing(false);
    }
  };

  const handleCreateDataset = async () => {
    setDatasetSaving(true);
    setDatasetResult(null);
    try {
      const result = await createDataset(queueId, datasetName, datasetLabelId === "" ? null : datasetLabelId);
      setDatasetResult(`Pushed "${result.name}": ${result.case_count} case(s), ${result.skipped_count} skipped.`);
    } catch (err) {
      setError(String(err));
    } finally {
      setDatasetSaving(false);
    }
  };

  if (selectedId === null) return <Navigate to="/annotators" replace />;
  if (error)
    return (
      <div className="page">
        <AppHeader />
        <main className="page-body">
          <p className="error-banner">{error}</p>
        </main>
      </div>
    );
  if (queue === null)
    return (
      <div className="page">
        <AppHeader />
        <main className="page-body">
          <p className="loading-text">Loading…</p>
        </main>
      </div>
    );
  if (!queue.is_accessible)
    return (
      <div className="page">
        <AppHeader />
        <main className="page-body">
          <p className="error-banner">You're not assigned to this queue.</p>
        </main>
      </div>
    );

  const exploreLink = items.find((item) => item.trace_url)?.trace_url;
  const exploreBase = exploreLink ? exploreLink.split("?")[0] + "/explore" : null;

  return (
    <div className="page page-wide">
      <AppHeader />
      <main className="page-body">
        <div className="page-heading page-heading-row">
          <div>
            <h1>{queue.name}</h1>
            {queue.criteria_text && <p className="queue-criteria">{queue.criteria_text}</p>}
          </div>
          <div className="queue-detail-actions">
            <Link className="btn btn-secondary" to={`/queues/${queue.id}/edit`}>
              Edit
            </Link>
            <button className="btn btn-secondary" onClick={handleRefresh} disabled={refreshing}>
              {refreshing ? "Refreshing…" : "Refresh"}
            </button>
            {exploreBase && (
              <a className="btn-link" href={`${exploreBase}?q=${encodeURIComponent(queue.query)}`} target="_blank" rel="noopener noreferrer">
                Open in Logfire Explore ↗
              </a>
            )}
            <button className="btn btn-primary" onClick={() => setShowDatasetForm((v) => !v)}>
              Create dataset
            </button>
          </div>
        </div>
        {refreshMessage && <p className="status-message status-message-success">{refreshMessage}</p>}

        {showDatasetForm && (
          <section className="card dataset-form">
            <div className="field">
              <label htmlFor="dataset-name">Dataset name</label>
              <input id="dataset-name" value={datasetName} onChange={(e) => setDatasetName(e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="dataset-label">Only include this label (optional)</label>
              <select
                id="dataset-label"
                value={datasetLabelId}
                onChange={(e) => setDatasetLabelId(e.target.value === "" ? "" : Number(e.target.value))}
              >
                <option value="">All annotated items</option>
                {queue.labels.map((label) => (
                  <option key={label.id} value={label.id}>
                    {label.name}
                  </option>
                ))}
              </select>
            </div>
            <button className="btn btn-primary" onClick={handleCreateDataset} disabled={datasetSaving || !datasetName}>
              {datasetSaving ? "Pushing…" : "Push to Logfire"}
            </button>
            {datasetResult && <p className="status-message status-message-success">{datasetResult}</p>}
          </section>
        )}

        <div className="interaction-list">
          {items.map((item) => (
            <QueueItemRow
              key={`${item.trace_id}:${item.span_id}`}
              queueId={queue.id}
              annotatorId={selectedId}
              item={item}
              labels={queue.labels}
            />
          ))}
        </div>
        {nextCursor && (
          <button className="btn btn-secondary load-more-btn" onClick={() => loadItems(nextCursor)} disabled={loading}>
            {loading ? "Loading…" : "Load more"}
          </button>
        )}
      </main>
    </div>
  );
}
```

- [ ] **Step 3: Add CSS for the new elements**

Append to `apps/annotation-studio/frontend/src/index.css`:

```css
.queue-detail-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.queue-criteria {
  color: var(--color-text-muted);
  margin-top: 4px;
  white-space: pre-wrap;
}

.query-helpers {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
}

.query-textarea {
  font-family: var(--font-mono);
  font-size: 0.9em;
}

.query-actions {
  display: flex;
  gap: 16px;
  margin-top: 6px;
}

.annotator-checkbox-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.annotator-checkbox {
  display: flex;
  align-items: center;
  gap: 8px;
}

.dataset-form {
  margin-bottom: 16px;
}
```

- [ ] **Step 4: Verify**

Run: `cd apps/annotation-studio/frontend && npx tsc --noEmit`
Expected: no type errors anywhere in `src/` now (Task 17 still needs to wire `App.tsx`'s routes, but every component/type reference is now consistent).

- [ ] **Step 5: Commit**

```bash
git add apps/annotation-studio/frontend/src/pages/QueueDetail.tsx apps/annotation-studio/frontend/src/components/QueueItemRow.tsx apps/annotation-studio/frontend/src/index.css
git commit -m "annotation-studio: add queue detail page with refresh, Explore link, and dataset export"
```

---

## Task 17: Routing and end-to-end verification

**Files:**
- Modify: `apps/annotation-studio/frontend/src/App.tsx`

**Interfaces:**
- Consumes: `ProjectList`, `ProjectDetail`, `Annotators`, `QueueForm`, `QueueDetail` (all prior tasks).

- [ ] **Step 1: Wire the routes**

Replace `apps/annotation-studio/frontend/src/App.tsx` in full:

```tsx
import { Route, Routes } from "react-router-dom";

import { Annotators } from "./pages/Annotators";
import { ProjectDetail } from "./pages/ProjectDetail";
import { ProjectList } from "./pages/ProjectList";
import { QueueDetail } from "./pages/QueueDetail";
import { QueueForm } from "./pages/QueueForm";

export function App() {
  return (
    <Routes>
      <Route path="/" element={<ProjectList />} />
      <Route path="/annotators" element={<Annotators />} />
      <Route path="/projects/:id" element={<ProjectDetail />} />
      <Route path="/projects/:id/queues/new" element={<QueueForm />} />
      <Route path="/queues/:queueId" element={<QueueDetail />} />
      <Route path="/queues/:queueId/edit" element={<QueueForm />} />
    </Routes>
  );
}
```

- [ ] **Step 2: Full build check**

Run: `cd apps/annotation-studio/frontend && npm run build`
Expected: builds cleanly with no TypeScript errors.

- [ ] **Step 3: Backend full test suite**

Run: `uv sync --all-packages && uv run pytest apps/annotation-studio/tests/ -v`
Expected: PASS for every test across all files.

- [ ] **Step 4: Manual smoke test**

Delete any stale local database (`rm -f apps/annotation-studio/data/annotation_studio.sqlite3`, or whatever `ANNOTATION_STUDIO_DATABASE_PATH` points to in your `.env` — this is the breaking-schema-change cleanup called out in Global Constraints), add `RX_ASSISTANT_LOGFIRE_DATASETS_TOKEN` to `apps/annotation-studio/.env` (a real token scoped `project:write_datasets`, minted the same way the existing read/write tokens were), then run both dev processes:

```bash
cd apps/annotation-studio/frontend && npm run dev
```
```bash
uv run --package annotation-studio uvicorn annotation_studio.main:app --reload
```

In a browser: create an annotator profile; open the project and confirm the seeded "All rx_assistant interactions" queue card appears; click "Refresh" on it and confirm items load (real Logfire data, since this isn't mocked in manual testing); expand an item, grade it, confirm write-back status shows; click "New queue," try each query helper button, save a second queue with a tool-call query and confirm its items render as raw JSON (no input/output shape); assign a specific annotator to a queue, switch to a different annotator profile, and confirm that queue disappears from the list and its detail page shows "not assigned"; grade at least one item, then use "Create dataset" and confirm the reported case/skip counts, then verify the dataset appears in Logfire's own UI.

Report any UI issues found and fix them before considering this task done — this is the acceptance bar for the whole plan, not just this task.

- [ ] **Step 5: Commit**

```bash
git add apps/annotation-studio/frontend/src/App.tsx
git commit -m "annotation-studio: wire queue routes"
```

---

## Post-plan cleanup note

The v1 design doc (`docs/superpowers/specs/2026-08-28-annotation-studio-design.md`) describes the pre-queues architecture and is now historical — leave it as-is (specs are a record of what was decided when, not living docs) rather than editing it to match the new design; `2026-08-31-annotation-studio-queues-design.md` is the current source of truth for this area.
