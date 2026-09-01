import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

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
    writeback_status TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'written' | 'failed'
    writeback_error TEXT,
    written_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(queue_id, trace_id, span_id, annotator_id)
);
"""


class ValidationError(ValueError):
    """A caller-supplied value is invalid — maps to HTTP 400 in routes.py. Subclasses
    ValueError (not bare Exception) so it satisfies `pytest.raises(ValueError)` wherever a
    caller only cares that validation failed, not which module's validator caught it."""


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


def seed_default_project(conn: sqlite3.Connection, name: str) -> None:
    if conn.execute("SELECT id FROM projects LIMIT 1").fetchone() is not None:
        return
    now = _now()
    conn.execute(
        "INSERT INTO projects (name, created_at, updated_at) VALUES (?, ?, ?)",
        (name, now, now),
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


def update_project(conn: sqlite3.Connection, project_id: int, name: str) -> dict:
    name = name.strip()
    if not name:
        raise ValidationError("Project name cannot be empty")
    conn.execute(
        "UPDATE projects SET name = ?, updated_at = ? WHERE id = ?", (name, _now(), project_id)
    )
    conn.commit()
    return get_project(conn, project_id)


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


def clear_queue_items(conn: sqlite3.Connection, queue_id: int) -> None:
    """Wipes a queue's current membership so the next refresh rebuilds it from scratch — for
    iterating on a query during setup, not for normal steady-state use (where membership is
    meant to only ever grow). Deliberately leaves `annotations` untouched: they're addressed
    by (queue_id, trace_id, span_id) independent of `queue_items`, so any existing grade
    reappears automatically if that same trace/span is rediscovered by a later refresh."""
    conn.execute("DELETE FROM queue_items WHERE queue_id = ?", (queue_id,))
    conn.execute("UPDATE queues SET last_refreshed_at = NULL WHERE id = ?", (queue_id,))
    conn.commit()


def list_queue_items(
    conn: sqlite3.Connection, queue_id: int, cursor: str | None, limit: int
) -> tuple[list[dict], str | None]:
    params: list = [queue_id]
    sql = "SELECT * FROM queue_items WHERE queue_id = ? "
    if cursor is not None:
        sql += "AND id < ? "
        params.append(int(cursor))
    sql += "ORDER BY id DESC LIMIT ?"
    params.append(limit + 1)
    rows = [_row_to_dict(row) for row in conn.execute(sql, params).fetchall()]
    # The cursor must be the last row actually returned on this page (rows[limit - 1]), not
    # the peeked-ahead extra row (rows[limit]) — using the peeked row's id here would make the
    # next page's exclusive `id < ?` predicate skip that very row instead of starting from it.
    next_cursor = str(rows[limit - 1]["id"]) if len(rows) > limit else None
    return rows[:limit], next_cursor


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
    conn: sqlite3.Connection, queue_id: int, trace_id: str, span_id: str, annotator_id: int
) -> dict | None:
    row = conn.execute(
        "SELECT * FROM annotations WHERE queue_id = ? AND trace_id = ? AND span_id = ? "
        "AND annotator_id = ?",
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
            "INSERT INTO annotations (queue_id, trace_id, span_id, annotator_id, label_id, "
            "description, revision, writeback_status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, 'pending', ?, ?)",
            (queue_id, trace_id, span_id, annotator_id, label_id, description, now, now),
        )
    conn.commit()
    return get_annotation(conn, queue_id, trace_id, span_id, annotator_id)


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


def list_annotations_for_dataset(conn: sqlite3.Connection, queue_id: int, label_id: int | None) -> list[dict]:
    sql = "SELECT * FROM annotations WHERE queue_id = ? AND label_id IS NOT NULL"
    params: list = [queue_id]
    if label_id is not None:
        sql += " AND label_id = ?"
        params.append(label_id)
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(row) for row in rows]
