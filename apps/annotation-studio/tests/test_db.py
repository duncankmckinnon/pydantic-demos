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
