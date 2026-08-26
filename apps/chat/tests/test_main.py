import chat.main
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel


def _fixed_reply_model(text: str = "success (no tool calls)") -> FunctionModel:
    # chat_agent always carries WebSearch(), a native tool — TestModel unconditionally rejects
    # any agent with a native tool attached ("TestModel does not support built-in tools"), so
    # FunctionModel (which explicitly supports built-in tools) stands in for it in these tests.
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(text)])

    return FunctionModel(respond)


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
    monkeypatch.setattr(chat.main, "get_model", lambda api_format, model_name, settings: _fixed_reply_model())
    client = TestClient(app)

    response = client.post(
        "/api/chat",
        json={"message": "hello", "model_choice": "anthropic:claude-sonnet-5"},
    )

    assert response.status_code == 200
    assert response.json() == {"reply": "success (no tool calls)"}
    assert "session_id" in response.cookies


def test_chat_endpoint_reuses_session_history(monkeypatch) -> None:
    app = chat.main.create_chat_app(send_to_logfire=False)
    monkeypatch.setattr(chat.main, "get_model", lambda api_format, model_name, settings: _fixed_reply_model())
    client = TestClient(app)

    first = client.post(
        "/api/chat", json={"message": "hello", "model_choice": "anthropic:claude-sonnet-5"}
    )
    session_cookie = first.cookies["session_id"]
    client.cookies.set("session_id", session_cookie)

    second = client.post(
        "/api/chat", json={"message": "again", "model_choice": "anthropic:claude-sonnet-5"}
    )

    assert second.status_code == 200
    assert len(chat.main._SESSIONS[session_cookie]) > 2


def test_chat_endpoint_rejects_unknown_model_choice(monkeypatch) -> None:
    app = chat.main.create_chat_app(send_to_logfire=False)
    monkeypatch.setattr(chat.main, "get_model", lambda api_format, model_name, settings: _fixed_reply_model())
    client = TestClient(app)

    # No colon at all — used to raise ValueError and surface as a 500.
    response = client.post("/api/chat", json={"message": "hi", "model_choice": "nonsense"})
    assert response.status_code == 400

    # Well-formed but not an offered choice.
    response = client.post(
        "/api/chat", json={"message": "hi", "model_choice": "anthropic:not-a-real-model"}
    )
    assert response.status_code == 400


def test_chat_endpoint_ignores_non_uuid_session_cookie(monkeypatch) -> None:
    app = chat.main.create_chat_app(send_to_logfire=False)
    monkeypatch.setattr(chat.main, "get_model", lambda api_format, model_name, settings: _fixed_reply_model())
    client = TestClient(app)
    client.cookies.set("session_id", "../../etc/passwd")

    response = client.post(
        "/api/chat", json={"message": "hello", "model_choice": "anthropic:claude-sonnet-5"}
    )

    assert response.status_code == 200
    assert "../../etc/passwd" not in chat.main._SESSIONS
    set_cookie = response.headers["set-cookie"]
    assert "httponly" in set_cookie.lower()
    assert "samesite=lax" in set_cookie.lower()
