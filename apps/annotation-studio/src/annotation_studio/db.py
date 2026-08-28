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
            try:
                validate_agent_name(top_level_agent_name)
            except ValueError as exc:
                # validate_agent_name (logfire_client, Task 3) raises plain ValueError, not
                # this module's ValidationError — translate so routes.py's single
                # `except db.ValidationError` (Task 5) maps every validation failure here to
                # HTTP 400, not just the ones raised directly by this function.
                raise ValidationError(str(exc)) from exc
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
                    keep_ids.add(label.id)

            # Delete removed labels first to avoid UNIQUE constraint violations
            for removed_id in existing_ids - keep_ids:
                conn.execute("DELETE FROM labels WHERE id = ?", (removed_id,))

            # Then update existing labels and insert new ones
            for order, label in enumerate(labels):
                if label.id is not None:
                    conn.execute(
                        "UPDATE labels SET name = ?, sort_order = ? WHERE id = ?",
                        (label.name, order, label.id),
                    )
                else:
                    cursor = conn.execute(
                        "INSERT INTO labels (project_id, name, sort_order) VALUES (?, ?, ?)",
                        (project_id, label.name, order),
                    )
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
