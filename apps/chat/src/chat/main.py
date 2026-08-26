import os
import uuid
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model

from chat.agent import MODEL_CHOICES, build_agent
from demo_core.logfire_setup import configure_logfire
from demo_core.models import get_model
from demo_core.settings import GatewaySettings, LogfireSettings
from demo_core.web import create_app

# NB: importing this module imports the `chat` package first, whose __init__ loads
# apps/chat/.env — so it is already in place before any Settings object below.
_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# Unsynchronized, unbounded, in-memory-only conversation store: it races under concurrent
# requests, never evicts, and is lost on restart. Acceptable for a local single-user demo
# only — anything else needs a real session store.
_SESSIONS: dict[str, list[ModelMessage]] = {}

# Cached per model_choice so each chat request reuses one Gateway provider (and its HTTP
# connection pool) instead of building a fresh one per message. Module-level and shared
# across create_chat_app() calls, like _SESSIONS; populated lazily inside the route so
# tests that monkeypatch get_model after app construction still take effect.
_MODEL_CACHE: dict[str, Model] = {}
_VALID_MODEL_CHOICES = {f"{fmt}:{name}" for fmt, name in MODEL_CHOICES}


class ChatRequest(BaseModel):
    message: str
    model_choice: str


class ChatResponse(BaseModel):
    reply: str


def _resolve_session_id(request: Request) -> str:
    """Return the request's session id, ignoring any cookie that isn't a valid UUID."""
    cookie = request.cookies.get("session_id")
    if cookie is not None:
        try:
            uuid.UUID(cookie)
        except ValueError:
            return str(uuid4())
        return cookie
    return str(uuid4())


def create_chat_app(send_to_logfire: bool | None = None) -> FastAPI:
    if send_to_logfire is None:
        # Lets the test suite (see tests/conftest.py) force offline mode before this
        # module's own `app = create_chat_app()` line runs at import time.
        send_to_logfire = os.environ.get("LOGFIRE_SEND_TO_LOGFIRE", "true").lower() != "false"

    logfire_settings = LogfireSettings()
    configure_logfire("chat", send_to_logfire=send_to_logfire, token=logfire_settings.token)
    app = create_app(title="Chat Demo")

    gateway_settings = GatewaySettings()
    agent = build_agent(gateway_settings)

    @app.get("/")
    async def index(request: Request):
        return _TEMPLATES.TemplateResponse(
            request, "index.html", {"model_choices": MODEL_CHOICES}
        )

    @app.post("/api/chat", response_model=ChatResponse)
    async def post_chat(payload: ChatRequest, request: Request, response: Response) -> ChatResponse:
        if payload.model_choice not in _VALID_MODEL_CHOICES:
            raise HTTPException(
                status_code=400, detail=f"Unknown model_choice: {payload.model_choice!r}"
            )

        session_id = _resolve_session_id(request)
        history = _SESSIONS.get(session_id, [])

        if payload.model_choice not in _MODEL_CACHE:
            api_format, model_name = payload.model_choice.split(":", 1)
            _MODEL_CACHE[payload.model_choice] = get_model(
                api_format, model_name, gateway_settings
            )
        model = _MODEL_CACHE[payload.model_choice]

        with agent.override(model=model):
            result = await agent.run(payload.message, message_history=history)

        _SESSIONS[session_id] = result.all_messages()
        response.set_cookie("session_id", session_id, httponly=True, samesite="lax")
        return ChatResponse(reply=str(result.output))

    return app


app = create_chat_app()
