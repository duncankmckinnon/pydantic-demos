from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from pydantic_ai.messages import ModelMessage

from chat.agent import MODEL_CHOICES, build_agent
from demo_core.logfire_setup import configure_logfire
from demo_core.models import get_model
from demo_core.settings import GatewaySettings
from demo_core.web import create_app

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
_SESSIONS: dict[str, list[ModelMessage]] = {}


class ChatRequest(BaseModel):
    message: str
    model_choice: str


class ChatResponse(BaseModel):
    reply: str


def create_chat_app(send_to_logfire: bool = True) -> FastAPI:
    configure_logfire("chat", send_to_logfire=send_to_logfire)
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
        session_id = request.cookies.get("session_id") or str(uuid4())
        history = _SESSIONS.get(session_id, [])

        api_format, model_name = payload.model_choice.split(":", 1)
        model = get_model(api_format, model_name, gateway_settings)

        with agent.override(model=model):
            result = await agent.run(payload.message, message_history=history)

        _SESSIONS[session_id] = result.all_messages()
        response.set_cookie("session_id", session_id)
        return ChatResponse(reply=str(result.output))

    return app


app = create_chat_app()
