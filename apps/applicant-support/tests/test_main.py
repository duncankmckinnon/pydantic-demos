import uuid

import applicant_support.main
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel


def _fixed_reply_model(text: str = "Here is your application update.") -> FunctionModel:
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(text)])

    return FunctionModel(respond)


def _valid_payload(**overrides) -> dict[str, str]:
    payload = {
        "application_id": "APP-1001",
        "message": "Did I pass screening?",
        "model_choice": "anthropic:claude-sonnet-5",
    }
    payload.update(overrides)
    return payload


def test_index_renders_applicant_portal_controls(tmp_path) -> None:
    app = applicant_support.main.create_applicant_support_app(
        send_to_logfire=False, database_path=tmp_path / "applicants.sqlite3"
    )

    response = TestClient(app).get("/")

    assert response.status_code == 200
    assert "Applicant Support" in response.text
    assert 'id="application-id"' in response.text
    assert "APP-1001" in response.text
    assert "Did I pass the screening?" in response.text


def test_chat_returns_reply_and_sets_safe_session_cookie(monkeypatch, tmp_path) -> None:
    app = applicant_support.main.create_applicant_support_app(
        send_to_logfire=False, database_path=tmp_path / "applicants.sqlite3"
    )
    monkeypatch.setattr(
        applicant_support.main,
        "get_model",
        lambda api_format, model_name, settings: _fixed_reply_model(),
    )

    response = TestClient(app).post("/api/chat", json=_valid_payload())

    assert response.status_code == 200
    assert response.json() == {"reply": "Here is your application update."}
    assert uuid.UUID(response.cookies["session_id"])
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=lax" in response.headers["set-cookie"].lower()


def test_chat_reuses_history_for_same_session(monkeypatch, tmp_path) -> None:
    app = applicant_support.main.create_applicant_support_app(
        send_to_logfire=False, database_path=tmp_path / "applicants.sqlite3"
    )
    monkeypatch.setattr(
        applicant_support.main,
        "get_model",
        lambda api_format, model_name, settings: _fixed_reply_model(),
    )
    client = TestClient(app)

    first = client.post("/api/chat", json=_valid_payload())
    session_id = first.cookies["session_id"]
    client.cookies.set("session_id", session_id)
    second = client.post(
        "/api/chat", json=_valid_payload(message="Can I reschedule my call?")
    )

    assert second.status_code == 200
    stored_application_id, messages = applicant_support.main._SESSIONS[session_id]
    assert stored_application_id == "APP-1001"
    assert len(messages) > 2


def test_changing_application_id_does_not_reuse_prior_history(monkeypatch, tmp_path) -> None:
    app = applicant_support.main.create_applicant_support_app(
        send_to_logfire=False, database_path=tmp_path / "applicants.sqlite3"
    )
    monkeypatch.setattr(
        applicant_support.main,
        "get_model",
        lambda api_format, model_name, settings: _fixed_reply_model(),
    )
    client = TestClient(app)

    first = client.post("/api/chat", json=_valid_payload())
    session_id = first.cookies["session_id"]
    client.cookies.set("session_id", session_id)
    second = client.post(
        "/api/chat", json=_valid_payload(application_id="APP-1002")
    )

    stored_application_id, messages = applicant_support.main._SESSIONS[session_id]
    assert second.status_code == 200
    assert stored_application_id == "APP-1002"
    assert len(messages) == 2


def test_chat_rejects_unknown_model_choice(tmp_path) -> None:
    app = applicant_support.main.create_applicant_support_app(
        send_to_logfire=False, database_path=tmp_path / "applicants.sqlite3"
    )

    response = TestClient(app).post(
        "/api/chat", json=_valid_payload(model_choice="anthropic:not-a-model")
    )

    assert response.status_code == 400


def test_chat_rejects_blank_or_malformed_application_id(tmp_path) -> None:
    app = applicant_support.main.create_applicant_support_app(
        send_to_logfire=False, database_path=tmp_path / "applicants.sqlite3"
    )
    client = TestClient(app)

    blank = client.post("/api/chat", json=_valid_payload(application_id=""))
    malformed = client.post(
        "/api/chat", json=_valid_payload(application_id="../../etc/passwd")
    )

    assert blank.status_code == 422
    assert malformed.status_code == 422


def test_chat_replaces_forged_session_cookie(monkeypatch, tmp_path) -> None:
    app = applicant_support.main.create_applicant_support_app(
        send_to_logfire=False, database_path=tmp_path / "applicants.sqlite3"
    )
    monkeypatch.setattr(
        applicant_support.main,
        "get_model",
        lambda api_format, model_name, settings: _fixed_reply_model(),
    )
    client = TestClient(app)
    client.cookies.set("session_id", "../../etc/passwd")

    response = client.post("/api/chat", json=_valid_payload())

    assert response.status_code == 200
    assert "../../etc/passwd" not in applicant_support.main._SESSIONS
    assert uuid.UUID(response.cookies["session_id"])
