import os
from pathlib import Path

import logfire
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from audio_demo.agent import MODEL_NAME, build_agent
from audio_demo.conversation import relay_conversation
from demo_core.logfire_setup import configure_logfire
from demo_core.models import get_realtime_model
from demo_core.settings import GatewaySettings, LogfireSettings
from demo_core.web import create_app

STATIC = Path(__file__).parent / "static"


def create_audio_app(send_to_logfire: bool | None = None) -> FastAPI:
    if send_to_logfire is None:
        send_to_logfire = os.environ.get("LOGFIRE_SEND_TO_LOGFIRE", "true").lower() != "false"
    configure_logfire("audio-demo", send_to_logfire=send_to_logfire, token=LogfireSettings().token)
    agent = build_agent()
    model = get_realtime_model(MODEL_NAME, GatewaySettings())
    app = create_app(title="Audio Demo")
    app.state.agent = agent
    app.state.realtime_model = model
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.websocket("/api/conversation")
    async def conversation(socket: WebSocket):
        # Only this app's browser page may open a metered voice session.
        host = socket.headers.get("host", "")
        if socket.headers.get("origin") not in {f"http://{host}", f"https://{host}"}:
            await socket.close(code=1008)
            return
        await socket.accept()
        try:
            realtime = app.state.agent.realtime(app.state.realtime_model)
            # Native Pydantic AI instrumentation creates the agent run, speech,
            # response/usage, and tool spans. Retain transcripts, not raw audio.
            # The browser owns playback, so the relay supplies its actual playhead
            # to session.interrupt() instead of using local-device auto barge-in.
            async with realtime.session(audio_retention="transcript_only") as session:
                await relay_conversation(socket, session)
        except WebSocketDisconnect:
            return
        except Exception:
            logfire.exception("Voice conversation failed")
            try:
                await socket.send_json({
                    "type": "error",
                    "message": "Conversation ended unexpectedly. Check the Gateway credentials and the agent trace in Logfire.",
                })
                await socket.close(code=1011)
            except (WebSocketDisconnect, RuntimeError):
                pass
        else:
            try:
                await socket.close(code=1000)
            except (WebSocketDisconnect, RuntimeError):
                pass

    return app


app = create_audio_app()
