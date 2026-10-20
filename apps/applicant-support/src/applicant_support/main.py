import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model

from applicant_support.agent import AgentDeps, MODEL_CHOICES, build_agent
from applicant_support.config import ResolvedAgentConfig, resolve_agent_config
from applicant_support.db import ApplicantDatabase
from applicant_support.service import run_applicant_turn
from demo_core.logfire_setup import configure_logfire
from demo_core.models import get_model
from demo_core.settings import GatewaySettings, LogfireSettings
from demo_core.web import create_app

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
_DEFAULT_DATABASE_PATH = Path(__file__).resolve().parents[2] / "data" / "applicants.sqlite3"

@dataclass
class SessionRecord:
    application_id: str
    messages: list[ModelMessage]
    config: ResolvedAgentConfig


# Local-demo-only process memory. The resolved configuration remains fixed for a session,
# while changing application IDs starts a clean message history.
_SESSIONS: dict[str, SessionRecord] = {}
_MODEL_CACHE: dict[str, Model] = {}


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_id: str = Field(pattern=r"^APP-\d{4}$")
    message: str = Field(min_length=1, max_length=2_000)
    full_demo_evals: bool = False


class ChatResponse(BaseModel):
    reply: str
    model_name: str
    prompt_fingerprint: str


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
    agent = build_agent(gateway_settings)
    app = create_app(title="Applicant Support Demo")

    @app.get("/")
    async def index(request: Request):
        return _TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {
                "model_choices": MODEL_CHOICES,
                "sample_application_ids": ["APP-1001", "APP-1002", "APP-1003"],
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
                "force_online_evals": payload.full_demo_evals,
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

    return app


app = create_applicant_support_app()
