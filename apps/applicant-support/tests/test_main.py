import uuid

import applicant_support.main
import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from applicant_support.config import AgentConfig, ResolvedAgentConfig


def _fixed_reply_model(text: str = "Here is your application update.") -> FunctionModel:
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(text)])

    return FunctionModel(respond)


def _valid_payload(**overrides) -> dict[str, object]:
    payload = {
        "application_id": "APP-1001",
        "message": "Did I pass screening?",
    }
    payload.update(overrides)
    return payload


def _resolved_config(
    *,
    model_name: str = "claude-sonnet-5",
    fingerprint: str = "prompt-a123",
) -> ResolvedAgentConfig:
    api_format = "openai-responses" if model_name.startswith("gpt-") else "anthropic"
    return ResolvedAgentConfig(
        prompt=f"Prompt for {fingerprint}",
        model=AgentConfig(api_format=api_format, model_name=model_name),
        prompt_fingerprint=fingerprint,
        prompt_source="managed",
        model_source="managed",
    )


@pytest.fixture(autouse=True)
def _stable_managed_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        applicant_support.main, "resolve_agent_config", lambda: _resolved_config()
    )


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
    assert response.json() == {
        "reply": "Here is your application update.",
        "model_name": "anthropic:claude-sonnet-5",
        "prompt_fingerprint": "prompt-a123",
    }
    assert uuid.UUID(response.cookies["session_id"])
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=lax" in response.headers["set-cookie"].lower()


def test_chat_reuses_history_and_config_for_same_session(monkeypatch, tmp_path) -> None:
    first_config = _resolved_config()
    promoted_config = _resolved_config(
        model_name="gpt-5-mini", fingerprint="prompt-b456"
    )
    resolutions = iter([first_config, promoted_config])
    app = applicant_support.main.create_applicant_support_app(
        send_to_logfire=False, database_path=tmp_path / "applicants.sqlite3"
    )
    monkeypatch.setattr(
        applicant_support.main, "resolve_agent_config", lambda: next(resolutions)
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
    stored = applicant_support.main._SESSIONS[session_id]
    assert stored.application_id == "APP-1001"
    assert len(stored.messages) > 2
    assert stored.config == first_config
    assert second.json()["model_name"] == "anthropic:claude-sonnet-5"
    assert second.json()["prompt_fingerprint"] == "prompt-a123"


def test_delete_session_picks_up_promoted_config(monkeypatch, tmp_path) -> None:
    configs = iter(
        [
            _resolved_config(),
            _resolved_config(model_name="gpt-5-mini", fingerprint="prompt-b456"),
        ]
    )
    monkeypatch.setattr(
        applicant_support.main, "resolve_agent_config", lambda: next(configs)
    )
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
    old_session_id = first.cookies["session_id"]
    reset = client.delete("/api/session")
    second = client.post("/api/chat", json=_valid_payload())

    assert reset.status_code == 204
    assert old_session_id not in applicant_support.main._SESSIONS
    assert second.cookies["session_id"] != old_session_id
    assert second.json()["model_name"] == "openai-responses:gpt-5-mini"
    assert second.json()["prompt_fingerprint"] == "prompt-b456"


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

    stored = applicant_support.main._SESSIONS[session_id]
    assert second.status_code == 200
    assert stored.application_id == "APP-1002"
    assert len(stored.messages) == 2
    assert stored.config.prompt_fingerprint == "prompt-a123"


def test_chat_rejects_caller_supplied_model_choice(tmp_path) -> None:
    app = applicant_support.main.create_applicant_support_app(
        send_to_logfire=False, database_path=tmp_path / "applicants.sqlite3"
    )

    response = TestClient(app).post(
        "/api/chat", json=_valid_payload(model_choice="anthropic:not-a-model")
    )

    assert response.status_code == 422


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


def test_valid_but_unissued_cookie_cannot_join_another_client_session(
    monkeypatch, tmp_path
) -> None:
    message_counts: list[int] = []

    def record_history(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        message_counts.append(len(messages))
        return ModelResponse(parts=[TextPart("Application update")])

    app = applicant_support.main.create_applicant_support_app(
        send_to_logfire=False, database_path=tmp_path / "applicants.sqlite3"
    )
    monkeypatch.setattr(
        applicant_support.main,
        "get_model",
        lambda api_format, model_name, settings: FunctionModel(record_history),
    )
    forged_session_id = "00000000-0000-0000-0000-000000000000"
    first_client = TestClient(app)
    second_client = TestClient(app)
    first_client.cookies.set("session_id", forged_session_id)
    second_client.cookies.set("session_id", forged_session_id)

    first = first_client.post("/api/chat", json=_valid_payload())
    second = second_client.post("/api/chat", json=_valid_payload())

    assert first.cookies["session_id"] != forged_session_id
    assert second.cookies["session_id"] != forged_session_id
    assert first.cookies["session_id"] != second.cookies["session_id"]
    assert message_counts == [1, 1]
