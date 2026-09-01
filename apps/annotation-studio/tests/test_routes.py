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
    conn = sqlite3.connect(":memory:", check_same_thread=False)
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


def _seeded_queue_id(client: TestClient, project_id: int) -> int:
    return client.get(f"/api/projects/{project_id}/queues").json()[0]["id"]


def _pass_label_id(client: TestClient, queue_id: int) -> int:
    return next(l["id"] for l in client.get(f"/api/queues/{queue_id}").json()["labels"] if l["name"] == "Pass")


async def _async_return(value):
    return value


async def _noop_coro():
    return None


def test_list_projects_returns_seeded_project() -> None:
    client = _app()

    response = client.get("/api/projects")

    assert response.status_code == 200
    assert response.json()[0]["name"] == "rx-assistant"


def test_get_project_no_longer_includes_labels() -> None:
    client = _app()
    project_id = _project_id(client)

    response = client.get(f"/api/projects/{project_id}")

    assert "labels" not in response.json()


def test_get_project_returns_404_for_unknown_id() -> None:
    client = _app()

    assert client.get("/api/projects/999").status_code == 404


def test_put_project_renames() -> None:
    client = _app()
    project_id = _project_id(client)

    response = client.put(f"/api/projects/{project_id}", json={"name": "renamed"})

    assert response.status_code == 200
    assert response.json()["name"] == "renamed"


def test_get_logfire_info_returns_org_and_project(monkeypatch) -> None:
    async def fake_info(read_token):
        return {"base_url": "https://logfire-us.pydantic.dev", "organization_name": "duncan", "project_name": "rx-assistant-demo"}

    monkeypatch.setattr(routes, "fetch_logfire_project_info", fake_info)
    client = _app()
    project_id = _project_id(client)

    response = client.get(f"/api/projects/{project_id}/logfire-info")

    assert response.status_code == 200
    assert response.json() == {
        "base_url": "https://logfire-us.pydantic.dev",
        "organization_name": "duncan",
        "project_name": "rx-assistant-demo",
    }


def test_get_logfire_info_returns_404_for_unknown_project() -> None:
    client = _app()

    assert client.get("/api/projects/999/logfire-info").status_code == 404


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


def test_refresh_always_scans_the_full_lookback_window_even_after_a_prior_refresh(monkeypatch) -> None:
    # Regression: an earlier refresh (e.g. against a since-fixed, previously-broken query)
    # must not permanently narrow later refreshes to "since that refresh" — a query that only
    # now matches historical data must still find it.
    captured_min_timestamps = []

    async def fake_fetch_matches(read_token, query, min_timestamp, max_timestamp, limit):
        captured_min_timestamps.append(min_timestamp)
        return []

    monkeypatch.setattr(routes, "fetch_queue_matches", fake_fetch_matches)
    client = _app()
    project_id = _project_id(client)
    queue_id = _seeded_queue_id(client, project_id)

    client.post(f"/api/queues/{queue_id}/refresh")  # first refresh sets last_refreshed_at
    client.post(f"/api/queues/{queue_id}/refresh")  # second refresh must not narrow the window

    assert len(captured_min_timestamps) == 2
    # Both calls' min_timestamp must be at (or before) the full lookback floor, not the
    # first call's much-more-recent last_refreshed_at.
    from datetime import datetime, timedelta, timezone

    from annotation_studio.logfire_client import LOOKBACK_DAYS

    floor = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    assert captured_min_timestamps[1] <= floor + timedelta(minutes=1)


def test_refresh_returns_403_when_annotator_not_assigned(monkeypatch) -> None:
    monkeypatch.setattr(routes, "fetch_queue_matches", lambda *a, **k: _async_return([]))
    client = _app()
    project_id = _project_id(client)
    ada = _create_annotator(client, "Ada")
    outsider = _create_annotator(client, "Outsider")
    queue_id = _seeded_queue_id(client, project_id)
    client.put(f"/api/queues/{queue_id}", json={"annotator_ids": [ada["id"]]})

    response = client.post(f"/api/queues/{queue_id}/refresh?annotator_id={outsider['id']}")

    assert response.status_code == 403


def test_clear_wipes_existing_items_before_rebuilding(monkeypatch) -> None:
    trace_id = "01a045b8d6d40acd6c98ee00f1a3fe93"
    call_count = {"n": 0}

    async def fake_fetch_matches(read_token, query, min_timestamp, max_timestamp, limit):
        call_count["n"] += 1
        span_id = "c7a2373c3fe61d3f" if call_count["n"] == 1 else "d7a2373c3fe61d3f"
        return [{"trace_id": trace_id, "span_id": span_id, "start_timestamp": "2026-08-28T00:00:00Z"}]

    monkeypatch.setattr(routes, "fetch_queue_matches", fake_fetch_matches)
    monkeypatch.setattr(routes, "fetch_queue_item_content", lambda read_token, items: _async_return({}))
    client = _app()
    project_id = _project_id(client)
    queue_id = _seeded_queue_id(client, project_id)
    client.post(f"/api/queues/{queue_id}/refresh")  # seeds one item keyed on the first span_id

    response = client.post(f"/api/queues/{queue_id}/clear")

    assert response.status_code == 200
    assert response.json() == {"new_item_count": 1, "total_item_count": 1}
    items = client.get(f"/api/queues/{queue_id}/items?annotator_id={_create_annotator(client, 'Ada')['id']}").json()
    assert len(items["items"]) == 1
    assert items["items"][0]["span_id"] == "d7a2373c3fe61d3f"


def test_clear_returns_403_when_annotator_not_assigned(monkeypatch) -> None:
    monkeypatch.setattr(routes, "fetch_queue_matches", lambda *a, **k: _async_return([]))
    client = _app()
    project_id = _project_id(client)
    ada = _create_annotator(client, "Ada")
    outsider = _create_annotator(client, "Outsider")
    queue_id = _seeded_queue_id(client, project_id)
    client.put(f"/api/queues/{queue_id}", json={"annotator_ids": [ada["id"]]})

    response = client.post(f"/api/queues/{queue_id}/clear?annotator_id={outsider['id']}")

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
