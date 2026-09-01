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
    db.seed_default_project(conn)
    return db.list_projects(conn)[0]


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


def test_delete_referenced_annotator_raises_conflict() -> None:
    conn = _fresh_conn()
    project = _seeded_project(conn)
    queue = _queue(conn, project["id"])
    ada = db.create_annotator(conn, "Ada")
    pass_id = next(l["id"] for l in queue["labels"] if l["name"] == "Pass")
    db.upsert_annotation(conn, queue["id"], "t1", "s1", ada["id"], pass_id, "ok")

    with pytest.raises(db.ConflictError):
        db.delete_annotator(conn, ada["id"])


def test_mark_writeback_written_sets_status_and_written_at() -> None:
    conn = _fresh_conn()
    project = _seeded_project(conn)
    queue = _queue(conn, project["id"])
    pass_id = next(l["id"] for l in queue["labels"] if l["name"] == "Pass")
    ada = db.create_annotator(conn, "Ada")
    annotation = db.upsert_annotation(conn, queue["id"], "t1", "s1", ada["id"], pass_id, "ok")

    db.mark_writeback_written(conn, annotation["id"], annotation["revision"])

    reloaded = db.get_annotation(conn, queue["id"], "t1", "s1", ada["id"])
    assert reloaded["writeback_status"] == "written"
    assert reloaded["written_at"] is not None


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
