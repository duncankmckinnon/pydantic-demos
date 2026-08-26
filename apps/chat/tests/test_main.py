import chat.main
from fastapi.testclient import TestClient
from pydantic_ai.models.test import TestModel


def test_index_page_lists_model_choices() -> None:
    app = chat.main.create_chat_app(send_to_logfire=False)
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "Chat Demo" in response.text
    for api_format, model_name in chat.main.MODEL_CHOICES:
        assert f"{api_format}:{model_name}" in response.text


def test_chat_endpoint_returns_reply_and_sets_session_cookie(monkeypatch) -> None:
    app = chat.main.create_chat_app(send_to_logfire=False)
    monkeypatch.setattr(chat.main, "get_model", lambda api_format, model_name, settings: TestModel())
    client = TestClient(app)

    response = client.post(
        "/api/chat",
        json={"message": "hello", "model_choice": "anthropic:claude-sonnet-4-6"},
    )

    assert response.status_code == 200
    assert response.json() == {"reply": "success (no tool calls)"}
    assert "session_id" in response.cookies


def test_chat_endpoint_reuses_session_history(monkeypatch) -> None:
    app = chat.main.create_chat_app(send_to_logfire=False)
    monkeypatch.setattr(chat.main, "get_model", lambda api_format, model_name, settings: TestModel())
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
    assert len(chat.main._SESSIONS[session_cookie]) > 2
