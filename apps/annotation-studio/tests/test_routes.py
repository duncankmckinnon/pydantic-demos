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
    # check_same_thread=False: FastAPI's TestClient (starlette) dispatches each request
    # through an anyio blocking-portal thread distinct from this fixture's thread — same
    # reason db.get_connection() sets this for the production path.
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
