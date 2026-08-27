import os
import uuid
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model

from demo_core.logfire_setup import configure_logfire
from demo_core.models import get_model
from demo_core.settings import GatewaySettings, LogfireSettings
from demo_core.web import create_app
from rx_assistant.agent import Deps, MODEL_CHOICES, build_agent
from rx_assistant.db import create_pool
from rx_assistant.embeddings import load_embedding_model
from rx_assistant.evals.online import RX_ASSISTANT_ONLINE_EVALUATION
from rx_assistant.settings import DatabaseSettings

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# Unsynchronized, unbounded, in-memory-only conversation store — same tradeoff as
# chat.main._SESSIONS: fine for a local single-user demo only.
_SESSIONS: dict[str, list[ModelMessage]] = {}

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


def create_rx_app(send_to_logfire: bool | None = None, deps: Deps | None = None) -> FastAPI:
    if send_to_logfire is None:
        # Lets the test suite (see tests/conftest.py) force offline mode before this
        # module's own `app = create_rx_app()` line runs at import time.
        send_to_logfire = os.environ.get("LOGFIRE_SEND_TO_LOGFIRE", "true").lower() != "false"

    logfire_settings = LogfireSettings()
    configure_logfire("rx-assistant", send_to_logfire=send_to_logfire, token=logfire_settings.token)
    app = create_app(title="Rx Assistant Demo")

    gateway_settings = GatewaySettings()
    agent = build_agent(gateway_settings, capabilities=[RX_ASSISTANT_ONLINE_EVALUATION])

    # Holds the real (or test-double) Deps once available. A dict, not a bare variable,
    # so the on_event closures below can mutate it.
    _state: dict[str, Deps] = {}
    if deps is not None:
        _state["deps"] = deps

    # demo_core.web.create_app() doesn't expose a lifespan hook (no second demo needs one
    # yet, so it isn't built there) — on_event is the pragmatic way to run startup/shutdown
    # logic against the FastAPI instance it already returns, without touching demo_core.
    @app.on_event("startup")
    async def _startup() -> None:
        if "deps" in _state:
            return
        database_settings = DatabaseSettings()
        pool = await create_pool(database_settings.database_url)
        _state["deps"] = Deps(pool=pool, embedding_model=load_embedding_model())

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        deps_obj = _state.get("deps")
        if deps_obj is not None:
            await deps_obj.pool.close()

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
            result = await agent.run(
                payload.message, message_history=history, deps=_state["deps"]
            )

        _SESSIONS[session_id] = result.all_messages()
        response.set_cookie("session_id", session_id, httponly=True, samesite="lax")
        return ChatResponse(reply=str(result.output))

    return app


app = create_rx_app()
