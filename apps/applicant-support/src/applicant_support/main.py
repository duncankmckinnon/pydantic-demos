import asyncio
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model

from applicant_support.agent import AgentDeps, build_agent
from applicant_support.config import ResolvedAgentConfig, resolve_agent_config
from applicant_support.db import ApplicantDatabase
from applicant_support.evals.experiments import (
    ExperimentKind,
    ExperimentRequest,
    ExperimentRunSummary,
    build_experiment_request,
    run_full_comparison,
    run_smoke_comparison,
)
from applicant_support.evals.hosted import (
    HostedDataset,
    create_dataset_client,
    load_hosted_dataset,
)
from applicant_support.evals.online import APPLICANT_SUPPORT_ONLINE_EVALUATION
from applicant_support.jobs import ExperimentJob, ExperimentJobRegistry, ExperimentScope
from applicant_support.service import run_applicant_turn
from demo_core.logfire_setup import configure_logfire
from demo_core.models import get_model
from demo_core.settings import GatewaySettings, LogfireSettings
from demo_core.web import create_app

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
_DEFAULT_DATABASE_PATH = Path(__file__).resolve().parents[2] / "data" / "applicants.sqlite3"
_LOGFIRE_PROJECT_URL = "https://logfire-us.pydantic.dev/duncan/applicant-support"


@dataclass
class SessionRecord:
    application_id: str
    messages: list[ModelMessage]
    config: ResolvedAgentConfig


# Local-demo-only process memory. The resolved configuration remains fixed for a session,
# while changing application IDs starts a clean message history.
_SESSIONS: dict[str, SessionRecord] = {}
_MODEL_CACHE: dict[str, Model] = {}
_JOB_REGISTRY = ExperimentJobRegistry()
_MODEL_JUDGE_COUNT = 4


@dataclass
class ExperimentJobContext:
    request: ExperimentRequest
    dataset: HostedDataset | None = None
    confirmation_token: str = field(default_factory=lambda: uuid4().hex)
    full_launched: bool = False


_EXPERIMENT_CONTEXTS: dict[str, ExperimentJobContext] = {}


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_id: str = Field(pattern=r"^APP-\d{4}$")
    message: str = Field(min_length=1, max_length=2_000)


class ChatResponse(BaseModel):
    reply: str
    model_name: str
    prompt_fingerprint: str


class FullExperimentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    smoke_job_id: str
    confirmation_token: str


class ExperimentCandidatePreview(BaseModel):
    label: str
    model_name: str
    prompt_fingerprint: str


class FullExperimentPreview(BaseModel):
    kind: ExperimentKind
    smoke_job_id: str
    confirmation_token: str
    candidate_count: int
    case_count: int
    judge_count: int
    estimated_model_calls: int
    candidates: list[ExperimentCandidatePreview]


def _fetch_hosted_dataset() -> HostedDataset:
    with create_dataset_client() as client:
        return load_hosted_dataset(client)


async def _run_experiment_job(
    context: ExperimentJobContext,
    scope: ExperimentScope,
    gateway_settings: GatewaySettings,
) -> list[ExperimentRunSummary]:
    if context.dataset is None:
        context.dataset = await asyncio.to_thread(_fetch_hosted_dataset)

    dataset = context.dataset
    assert dataset is not None
    kind = context.request.kind
    if scope == "smoke":
        return await run_smoke_comparison(
            kind,
            dataset=dataset,
            gateway_settings=gateway_settings,
            request=context.request,
        )
    return await run_full_comparison(
        kind,
        dataset=dataset,
        gateway_settings=gateway_settings,
        request=context.request,
    )


def _resolve_session_id(request: Request) -> str:
    cookie = request.cookies.get("session_id")
    if cookie is not None:
        try:
            uuid.UUID(cookie)
        except ValueError:
            pass
        else:
            if cookie in _SESSIONS:
                return cookie
    return str(uuid4())


def create_applicant_support_app(
    send_to_logfire: bool | None = None,
    database_path: Path | str | None = None,
) -> FastAPI:
    if send_to_logfire is None:
        send_to_logfire = os.environ.get("LOGFIRE_SEND_TO_LOGFIRE", "true").lower() != "false"

    logfire_settings = LogfireSettings()
    configure_logfire(
        "applicant-support",
        send_to_logfire=send_to_logfire,
        token=logfire_settings.token,
    )

    database = ApplicantDatabase(
        database_path or os.environ.get("APPLICANT_DB_PATH", _DEFAULT_DATABASE_PATH)
    )
    database.initialize()

    gateway_settings = GatewaySettings()
    agent = build_agent(
        gateway_settings,
        capabilities=[APPLICANT_SUPPORT_ONLINE_EVALUATION],
    )
    app = create_app(title="Applicant Support Demo")

    @app.get("/")
    async def index(request: Request):
        return _TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {
                "sample_application_ids": ["APP-1001", "APP-1002", "APP-1003"],
                "logfire_project_url": _LOGFIRE_PROJECT_URL,
            },
        )

    @app.post("/api/chat", response_model=ChatResponse)
    async def post_chat(
        payload: ChatRequest, request: Request, response: Response
    ) -> ChatResponse:
        session_id = _resolve_session_id(request)
        stored_session = _SESSIONS.get(session_id)
        if stored_session is None:
            config = resolve_agent_config()
            history: list[ModelMessage] = []
        else:
            config = stored_session.config
            history = (
                stored_session.messages
                if stored_session.application_id == payload.application_id
                else []
            )

        model_choice = ":".join(config.model.model_choice)
        if model_choice not in _MODEL_CACHE:
            _MODEL_CACHE[model_choice] = get_model(
                *config.model.model_choice, gateway_settings
            )
        model = _MODEL_CACHE[model_choice]

        result = await run_applicant_turn(
            agent,
            question=payload.message,
            deps=AgentDeps(database=database, application_id=payload.application_id),
            config=config,
            history=history,
            metadata={
                "session_id": session_id,
            },
            model=model,
        )

        _SESSIONS[session_id] = SessionRecord(
            application_id=payload.application_id,
            messages=result.all_messages(),
            config=config,
        )
        response.set_cookie("session_id", session_id, httponly=True, samesite="lax")
        return ChatResponse(
            reply=str(result.output),
            model_name=model_choice,
            prompt_fingerprint=config.prompt_fingerprint,
        )

    @app.delete("/api/session", status_code=204)
    async def delete_session(request: Request, response: Response) -> None:
        session_id = request.cookies.get("session_id")
        if session_id is not None:
            _SESSIONS.pop(session_id, None)
        response.delete_cookie("session_id", httponly=True, samesite="lax")

    def launch_experiment(
        context: ExperimentJobContext,
        scope: ExperimentScope,
    ) -> ExperimentJob:
        job = _JOB_REGISTRY.launch(
            kind=context.request.kind,
            scope=scope,
            runner=lambda: _run_experiment_job(context, scope, gateway_settings),
        )
        _EXPERIMENT_CONTEXTS[job.id] = context
        return job

    def qualified_smoke_context(
        kind: ExperimentKind,
        smoke_job_id: str,
    ) -> ExperimentJobContext:
        try:
            smoke = _JOB_REGISTRY.get(smoke_job_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Smoke job not found") from None
        if (
            smoke.kind != kind
            or smoke.scope != "smoke"
            or smoke.status != "completed"
        ):
            raise HTTPException(
                status_code=409,
                detail="A completed smoke job of the same comparison kind is required",
            )

        context = _EXPERIMENT_CONTEXTS.get(smoke_job_id)
        if context is None or context.dataset is None:
            raise HTTPException(
                status_code=409,
                detail="The smoke job no longer has a prepared experiment context",
            )

        expected = [
            (
                candidate.label,
                ":".join(candidate.model.model_choice),
                candidate.prompt_fingerprint,
            )
            for candidate in context.request.candidates
        ]
        actual = [
            (
                summary.candidate,
                summary.model_name,
                summary.prompt_fingerprint,
            )
            for summary in smoke.summaries
        ]
        if actual != expected:
            raise HTTPException(
                status_code=409,
                detail="The prepared candidate configuration no longer matches the smoke run",
            )
        return context

    @app.post(
        "/api/experiments/{kind}/smoke",
        response_model=ExperimentJob,
        status_code=202,
    )
    async def post_smoke_experiment(kind: ExperimentKind) -> ExperimentJob:
        context = ExperimentJobContext(
            request=build_experiment_request(kind, control=resolve_agent_config())
        )
        return launch_experiment(context, "smoke")

    @app.get(
        "/api/experiments/{kind}/full-preview/{smoke_job_id}",
        response_model=FullExperimentPreview,
    )
    async def get_full_experiment_preview(
        kind: ExperimentKind,
        smoke_job_id: str,
    ) -> FullExperimentPreview:
        context = qualified_smoke_context(kind, smoke_job_id)
        candidates = [
            ExperimentCandidatePreview(
                label=candidate.label,
                model_name=":".join(candidate.model.model_choice),
                prompt_fingerprint=candidate.prompt_fingerprint,
            )
            for candidate in context.request.candidates
        ]
        case_count = len(context.dataset.cases)
        return FullExperimentPreview(
            kind=kind,
            smoke_job_id=smoke_job_id,
            confirmation_token=context.confirmation_token,
            candidate_count=len(candidates),
            case_count=case_count,
            judge_count=_MODEL_JUDGE_COUNT,
            estimated_model_calls=(
                len(candidates) * case_count * (1 + _MODEL_JUDGE_COUNT)
            ),
            candidates=candidates,
        )

    @app.post(
        "/api/experiments/{kind}/full",
        response_model=ExperimentJob,
        status_code=202,
    )
    async def post_full_experiment(
        kind: ExperimentKind,
        payload: FullExperimentRequest,
    ) -> ExperimentJob:
        context = qualified_smoke_context(kind, payload.smoke_job_id)
        if payload.confirmation_token != context.confirmation_token:
            raise HTTPException(
                status_code=409,
                detail="The full-run confirmation does not match the prepared scope",
            )
        if context.full_launched:
            raise HTTPException(
                status_code=409,
                detail="A full run has already been launched for this smoke job",
            )
        context.full_launched = True
        return launch_experiment(context, "full")

    @app.get(
        "/api/experiments/jobs/{job_id}",
        response_model=ExperimentJob,
    )
    async def get_experiment_job(job_id: str) -> ExperimentJob:
        try:
            return _JOB_REGISTRY.get(job_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Experiment job not found") from None

    return app


app = create_applicant_support_app()
