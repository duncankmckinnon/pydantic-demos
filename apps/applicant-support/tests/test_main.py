import uuid
import threading
from types import SimpleNamespace

import applicant_support.main
import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from applicant_support.config import AgentConfig, ResolvedAgentConfig
from applicant_support.evals.dataset import build_seed_dataset
from applicant_support.evals.experiments import (
    ExperimentRunSummary,
    build_experiment_request,
)
from applicant_support.main import ExperimentJobContext


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
    assert 'id="model-select"' not in response.text
    assert 'id="resolved-model"' in response.text
    assert 'id="prompt-fingerprint"' in response.text
    assert 'id="new-session"' in response.text
    assert 'id="full-demo-evals"' in response.text
    assert 'id="model-smoke"' in response.text
    assert 'id="prompt-smoke"' in response.text
    assert 'id="experiment-status"' in response.text
    assert "full-preview" in response.text
    assert "Estimated model calls" in response.text
    assert "Open Logfire" in response.text


def test_live_agent_is_built_with_online_evaluation(monkeypatch, tmp_path) -> None:
    from applicant_support.agent import build_agent as real_build_agent
    from applicant_support.evals.online import applicant_support_online_evaluation

    seen: dict[str, object] = {}

    def recording_build(settings, *, capabilities=()):
        seen["capabilities"] = capabilities
        return real_build_agent(settings, capabilities=capabilities)

    monkeypatch.setattr(applicant_support.main, "build_agent", recording_build)

    applicant_support.main.create_applicant_support_app(
        send_to_logfire=False, database_path=tmp_path / "applicants.sqlite3"
    )

    assert list(seen["capabilities"]) == [applicant_support_online_evaluation]


def test_chat_forwards_full_demo_evaluation_flag(monkeypatch, tmp_path) -> None:
    seen: dict[str, object] = {}

    async def fake_turn(agent, **kwargs):
        seen.update(kwargs["metadata"])
        return SimpleNamespace(output="Reply", all_messages=lambda: [])

    app = applicant_support.main.create_applicant_support_app(
        send_to_logfire=False, database_path=tmp_path / "applicants.sqlite3"
    )
    monkeypatch.setattr(applicant_support.main, "run_applicant_turn", fake_turn)
    monkeypatch.setattr(applicant_support.main, "get_model", lambda *args: object())

    response = TestClient(app).post(
        "/api/chat",
        json=_valid_payload(full_demo_evals=True),
    )

    assert response.status_code == 200
    assert seen["force_online_evals"] is True


def _experiment_summary(kind: str = "model") -> ExperimentRunSummary:
    return ExperimentRunSummary(
        kind=kind,
        comparison_group=f"{kind}-group",
        candidate="managed-control",
        experiment_name=f"applicant_support_{kind}_group_managed-control",
        task_name=f"applicant_support_agent:{kind}:managed-control",
        case_count=3,
        model_name="anthropic:claude-sonnet-5",
        prompt_fingerprint="abc123def456",
    )


def _context_summaries(
    context: ExperimentJobContext, *, case_count: int = 3
) -> list[ExperimentRunSummary]:
    return [
        ExperimentRunSummary(
            kind=context.request.kind,
            comparison_group=context.request.comparison_group,
            candidate=candidate.label,
            experiment_name=f"experiment-{candidate.label}",
            task_name=f"task-{candidate.label}",
            case_count=case_count,
            model_name=":".join(candidate.model.model_choice),
            prompt_fingerprint=candidate.prompt_fingerprint,
        )
        for candidate in context.request.candidates
    ]


def test_smoke_experiment_endpoint_enqueues_and_serializes_job(monkeypatch, tmp_path) -> None:
    seen: list[tuple[str, str]] = []

    async def fake_run(context, scope, gateway_settings):
        seen.append((context.request.kind, scope))
        context.dataset = build_seed_dataset()
        return _context_summaries(context)

    monkeypatch.setattr(applicant_support.main, "_run_experiment_job", fake_run)
    app = applicant_support.main.create_applicant_support_app(
        send_to_logfire=False, database_path=tmp_path / "applicants.sqlite3"
    )
    with TestClient(app) as client:
        launched = client.post("/api/experiments/model/smoke")
        job_id = launched.json()["id"]
        status = client.get(f"/api/experiments/jobs/{job_id}")

    assert launched.status_code == 202
    assert status.status_code == 200
    assert status.json()["kind"] == "model"
    assert status.json()["scope"] == "smoke"
    assert status.json()["status"] in {"queued", "running", "completed"}
    assert seen in ([], [("model", "smoke")])


def test_full_experiment_requires_successful_matching_smoke(monkeypatch, tmp_path) -> None:
    async def fake_run(context, scope, gateway_settings):
        return _context_summaries(context, case_count=len(context.dataset.cases))

    monkeypatch.setattr(applicant_support.main, "_run_experiment_job", fake_run)
    app = applicant_support.main.create_applicant_support_app(
        send_to_logfire=False, database_path=tmp_path / "applicants.sqlite3"
    )
    registry = applicant_support.main._JOB_REGISTRY

    failed = registry.create(kind="model", scope="smoke")
    registry.start(failed.id)
    registry.fail(failed.id, RuntimeError("failure"))
    mismatched = registry.create(kind="prompt", scope="smoke")
    registry.start(mismatched.id)
    registry.complete(mismatched.id, [_experiment_summary("prompt")])

    with TestClient(app) as client:
        unknown = client.post(
            "/api/experiments/model/full",
            json={"smoke_job_id": "missing", "confirmation_token": "invalid"},
        )
        failed_response = client.post(
            "/api/experiments/model/full",
            json={"smoke_job_id": failed.id, "confirmation_token": "invalid"},
        )
        mismatch_response = client.post(
            "/api/experiments/model/full",
            json={"smoke_job_id": mismatched.id, "confirmation_token": "invalid"},
        )

    assert unknown.status_code == 404
    assert failed_response.status_code == 409
    assert mismatch_response.status_code == 409


def test_full_preview_discloses_and_binds_billed_scope(monkeypatch, tmp_path) -> None:
    launched_contexts: list[ExperimentJobContext] = []

    async def fake_run(context, scope, gateway_settings):
        launched_contexts.append(context)
        return _context_summaries(context, case_count=len(context.dataset.cases))

    monkeypatch.setattr(applicant_support.main, "_run_experiment_job", fake_run)
    app = applicant_support.main.create_applicant_support_app(
        send_to_logfire=False, database_path=tmp_path / "applicants.sqlite3"
    )
    context = ExperimentJobContext(
        request=build_experiment_request("model", control=_resolved_config()),
        dataset=build_seed_dataset(),
    )
    registry = applicant_support.main._JOB_REGISTRY
    smoke = registry.create(kind="model", scope="smoke")
    registry.start(smoke.id)
    registry.complete(smoke.id, _context_summaries(context))
    applicant_support.main._EXPERIMENT_CONTEXTS[smoke.id] = context

    with TestClient(app) as client:
        preview_response = client.get(
            f"/api/experiments/model/full-preview/{smoke.id}"
        )
        preview = preview_response.json()
        rejected = client.post(
            "/api/experiments/model/full",
            json={"smoke_job_id": smoke.id, "confirmation_token": "wrong"},
        )
        launched = client.post(
            "/api/experiments/model/full",
            json={
                "smoke_job_id": smoke.id,
                "confirmation_token": preview["confirmation_token"],
            },
        )

    assert preview_response.status_code == 200
    assert preview["case_count"] == len(context.dataset.cases)
    assert preview["candidate_count"] == len(context.request.candidates)
    assert preview["judge_count"] == 4
    assert preview["estimated_model_calls"] == (
        preview["candidate_count"] * preview["case_count"] * 5
    )
    assert preview["candidates"] == [
        {
            "label": candidate.label,
            "model_name": ":".join(candidate.model.model_choice),
            "prompt_fingerprint": candidate.prompt_fingerprint,
        }
        for candidate in context.request.candidates
    ]
    assert rejected.status_code == 409
    assert launched.status_code == 202
    assert launched_contexts in ([], [context])


async def test_experiment_dataset_fetch_runs_off_event_loop(monkeypatch) -> None:
    loop_thread = threading.get_ident()
    fetch_threads: list[int] = []
    dataset = build_seed_dataset()

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    def fake_load(client):
        fetch_threads.append(threading.get_ident())
        return dataset

    async def fake_smoke(kind, **kwargs):
        return []

    monkeypatch.setattr(applicant_support.main, "create_dataset_client", FakeClient)
    monkeypatch.setattr(applicant_support.main, "load_hosted_dataset", fake_load)
    monkeypatch.setattr(applicant_support.main, "run_smoke_comparison", fake_smoke)
    context = ExperimentJobContext(
        request=build_experiment_request("model", control=_resolved_config())
    )

    await applicant_support.main._run_experiment_job(
        context,
        "smoke",
        applicant_support.main.GatewaySettings(api_key="pylf_v1_us_test-key"),
    )

    assert fetch_threads and fetch_threads[0] != loop_thread
    assert context.dataset is dataset


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
