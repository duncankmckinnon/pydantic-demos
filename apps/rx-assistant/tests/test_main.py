from types import SimpleNamespace

import rx_assistant.main
from fastapi.testclient import TestClient
from pydantic_ai.models.test import TestModel

from rx_assistant.agent import Deps


class FakePool:
    def __init__(self, medication_rows=None):
        self._medication_rows = medication_rows or []

    async def fetch(self, query, *args):
        return self._medication_rows

    async def close(self) -> None:
        pass


class FakeEmbeddingModel:
    async def embed_query(self, text):
        return SimpleNamespace(embeddings=[[0.0, 0.0, 0.0]])

    async def embed_documents(self, texts):
        return SimpleNamespace(embeddings=[[0.0, 0.0, 0.0] for _ in texts])


def _fake_deps() -> Deps:
    return Deps(pool=FakePool(), embedding_model=FakeEmbeddingModel())


def test_index_page_lists_model_choices() -> None:
    app = rx_assistant.main.create_rx_app(send_to_logfire=False, deps=_fake_deps())
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "Rx Assistant" in response.text
    assert "not medical advice" in response.text.lower()
    for api_format, model_name in rx_assistant.main.MODEL_CHOICES:
        assert f"{api_format}:{model_name}" in response.text


def test_chat_endpoint_returns_reply_and_sets_session_cookie(monkeypatch) -> None:
    app = rx_assistant.main.create_rx_app(send_to_logfire=False, deps=_fake_deps())
    # Excludes the SubAgents-provided delegate_task tool: TestModel's default call_tools="all"
    # would otherwise call it too, actually invoking the web-research sub-agent's real
    # Gateway-routed model.
    monkeypatch.setattr(
        rx_assistant.main,
        "get_model",
        lambda api_format, model_name, settings: TestModel(call_tools=["search_medications"]),
    )
    client = TestClient(app)

    response = client.post(
        "/api/chat",
        json={"message": "hello", "model_choice": "anthropic:claude-sonnet-4-6"},
    )

    assert response.status_code == 200
    assert isinstance(response.json()["reply"], str) and response.json()["reply"]
    assert "session_id" in response.cookies


def test_chat_endpoint_reuses_session_history(monkeypatch) -> None:
    app = rx_assistant.main.create_rx_app(send_to_logfire=False, deps=_fake_deps())
    # Excludes the SubAgents-provided delegate_task tool: TestModel's default call_tools="all"
    # would otherwise call it too, actually invoking the web-research sub-agent's real
    # Gateway-routed model.
    monkeypatch.setattr(
        rx_assistant.main,
        "get_model",
        lambda api_format, model_name, settings: TestModel(call_tools=["search_medications"]),
    )
    client = TestClient(app)

    first = client.post(
        "/api/chat", json={"message": "hello", "model_choice": "anthropic:claude-sonnet-4-6"}
    )
    session_cookie = first.cookies["session_id"]
    client.cookies.set("session_id", session_cookie)

    second = client.post(
        "/api/chat", json={"message": "again", "model_choice": "anthropic:claude-sonnet-4-6"}
    )

    assert second.status_code == 200
    assert len(rx_assistant.main._SESSIONS[session_cookie]) > 2


def test_chat_endpoint_rejects_unknown_model_choice(monkeypatch) -> None:
    app = rx_assistant.main.create_rx_app(send_to_logfire=False, deps=_fake_deps())
    # Excludes the SubAgents-provided delegate_task tool: TestModel's default call_tools="all"
    # would otherwise call it too, actually invoking the web-research sub-agent's real
    # Gateway-routed model.
    monkeypatch.setattr(
        rx_assistant.main,
        "get_model",
        lambda api_format, model_name, settings: TestModel(call_tools=["search_medications"]),
    )
    client = TestClient(app)

    response = client.post("/api/chat", json={"message": "hi", "model_choice": "nonsense"})
    assert response.status_code == 400

    response = client.post(
        "/api/chat", json={"message": "hi", "model_choice": "anthropic:not-a-real-model"}
    )
    assert response.status_code == 400


def test_startup_and_shutdown_use_real_deps_when_none_provided(monkeypatch) -> None:
    fake_pool = FakePool()
    close_calls = []

    async def fake_create_pool(database_url):
        return fake_pool

    def fake_load_embedding_model():
        return FakeEmbeddingModel()

    async def fake_close():
        close_calls.append(True)

    monkeypatch.setattr(fake_pool, "close", fake_close)
    monkeypatch.setattr(rx_assistant.main, "create_pool", fake_create_pool)
    monkeypatch.setattr(rx_assistant.main, "load_embedding_model", fake_load_embedding_model)

    app = rx_assistant.main.create_rx_app(send_to_logfire=False)

    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200

    assert close_calls == [True]
