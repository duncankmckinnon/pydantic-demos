import os
import uuid
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model

from applicant_support.agent import AgentDeps, MODEL_CHOICES, build_agent
from applicant_support.db import ApplicantDatabase
from demo_core.logfire_setup import configure_logfire
from demo_core.models import get_model
from demo_core.settings import GatewaySettings, LogfireSettings
from demo_core.web import create_app

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
_DEFAULT_DATABASE_PATH = Path(__file__).resolve().parents[2] / "data" / "applicants.sqlite3"

# Local-demo-only process memory. Each session stores the application ID alongside its
# messages so changing IDs cannot feed one applicant's history into another applicant's run.
_SESSIONS: dict[str, tuple[str, list[ModelMessage]]] = {}
_MODEL_CACHE: dict[str, Model] = {}
_VALID_MODEL_CHOICES = {f"{api_format}:{model_name}" for api_format, model_name in MODEL_CHOICES}


class ChatRequest(BaseModel):
    application_id: str = Field(pattern=r"^APP-\d{4}$")
    message: str = Field(min_length=1, max_length=2_000)
    model_choice: str


class ChatResponse(BaseModel):
    reply: str


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
        if payload.model_choice not in _VALID_MODEL_CHOICES:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown model_choice: {payload.model_choice!r}",
            )

        session_id = _resolve_session_id(request)
        stored_session = _SESSIONS.get(session_id)
        history = (
            stored_session[1]
            if stored_session is not None and stored_session[0] == payload.application_id
            else []
        )

        if payload.model_choice not in _MODEL_CACHE:
            api_format, model_name = payload.model_choice.split(":", 1)
            _MODEL_CACHE[payload.model_choice] = get_model(
                api_format, model_name, gateway_settings
            )
        model = _MODEL_CACHE[payload.model_choice]

        with agent.override(model=model):
            result = await agent.run(
                payload.message,
                deps=AgentDeps(database=database, application_id=payload.application_id),
                message_history=history,
            )

        _SESSIONS[session_id] = (payload.application_id, result.all_messages())
        response.set_cookie("session_id", session_id, httponly=True, samesite="lax")
        return ChatResponse(reply=str(result.output))

    return app


app = create_applicant_support_app()
