import asyncio
import base64
import json
from contextlib import asynccontextmanager

import logfire
import pytest
from fastapi.testclient import TestClient
from pydantic_ai import BinaryAudio
from pydantic_ai.realtime import RealtimeInputSpeechStartEvent
from pydantic_ai.realtime.codec import (
    AudioDelta, InputTranscript, OutputTranscript, RealtimeConnection,
    ResponseDone, SessionUsage, ToolCall, ToolResult, TruncateOutput,
)
from pydantic_ai.realtime.model import RealtimeModel
from pydantic_ai.usage import RequestUsage
from starlette.websockets import WebSocketDisconnect

from audio_demo.main import app


class ScriptedConnection(RealtimeConnection):
    def __init__(self):
        self.queue = asyncio.Queue()
        self.received = []
        self.started = False

    async def send(self, content):
        self.received.append(content)
        if isinstance(content, (BinaryAudio, str)) and not self.started:
            self.started = True
            for event in (
                InputTranscript("Check account 123", is_final=True, item_id="user-1"),
                ToolCall("call-1", tool_name="get_payment_history", args='{"account_id":"123"}'),
            ):
                await self.queue.put(event)
        elif isinstance(content, ToolResult):
            for event in (
                OutputTranscript("Your sample payment was $412.18.", is_final=True, item_id="reply-1"),
                AudioDelta(b"\x01\x00" * 2400, item_id="reply-1"),
                SessionUsage(RequestUsage(input_tokens=10, output_tokens=20)),
                ResponseDone(),
            ):
                await self.queue.put(event)
        elif isinstance(content, BinaryAudio) and content.data == b"\x02\x00":
            await self.queue.put(RealtimeInputSpeechStartEvent(item_id="user-2"))

    async def __aiter__(self):
        while True:
            event = await self.queue.get()
            if isinstance(event, Exception):
                raise event
            yield event


class ScriptedModel(RealtimeModel):
    def __init__(self):
        super().__init__(profile={"supports_interruption": True, "supports_output_truncation": True})
        self.connection = None
        self.closed = False

    @property
    def model_name(self):
        return "scripted-voice"

    @property
    def system(self):
        return "test"

    @asynccontextmanager
    async def connect(self, **kwargs):
        self.connection = ScriptedConnection()
        try:
            yield self.connection
        finally:
            self.closed = True


@pytest.fixture
def client(monkeypatch):
    model = ScriptedModel()
    monkeypatch.setattr(app.state, "realtime_model", model)
    with TestClient(app) as client:
        yield client, model


def until(socket, kind):
    # Bound the event count so a broken protocol fails instead of silently looping.
    for _ in range(30):
        message = socket.receive_json()
        assert message["type"] != "error", message
        if message["type"] == kind:
            return message
    pytest.fail(f"Never received {kind}")


def test_browser_shell_and_health(client):
    http, _ = client
    assert http.get("/health").json() == {"status": "ok"}
    assert "Start conversation" in http.get("/").text
    assert http.get("/static/pcm-worklet.js").status_code == 200


def test_actual_realtime_agent_emits_nested_traces_audio_and_transcripts(client, capfire):
    http, model = client
    logfire.instrument_pydantic_ai()
    with http.websocket_connect("/api/conversation", headers={"origin": "http://testserver"}) as socket:
        assert socket.receive_json()["input_sample_rate"] == 24000
        socket.send_bytes(b"\0\0" * 2400)
        messages = []
        while not (any(m["type"] == "audio" for m in messages) and
                   any(m.get("speaker") == "assistant" for m in messages) and
                   any(m.get("speaker") == "user" for m in messages)):
            message = socket.receive_json()
            assert message["type"] != "error", message
            messages.append(message)
        audio = next(m for m in messages if m["type"] == "audio")
        assert base64.b64decode(audio["data"]) == b"\x01\x00" * 2400
        socket.send_json({"type": "played", "id": audio["id"], "played_bytes": 4800})
        socket.send_json({"type": "stop"})
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()
        assert closed.value.code == 1000
    assert model.closed
    spans = [s for s in capfire.exporter.exported_spans if s.attributes.get("logfire.span_type") != "pending_span"]
    root = next(s for s in spans if s.attributes.get("gen_ai.operation.name") == "invoke_agent")
    assert root.attributes["gen_ai.agent.name"] == "audio_agent"
    assert root.attributes["pydantic_ai.realtime"] is True
    assert root.attributes.get("logfire.level_num", 9) < 17
    children = [s for s in spans if s.parent and s.parent.span_id == root.context.span_id]
    assert any(s.attributes.get("gen_ai.operation.name") == "chat" for s in children)
    assert any("get_payment_history" in s.name for s in spans)
    serialized = json.dumps(dict(root.attributes))
    assert "Check account 123" in serialized
    assert "$412.18" in serialized
    assert any(s.attributes.get("gen_ai.usage.output_tokens") == 20 for s in spans)


def test_barge_in_truncates_to_browser_playback(client):
    http, model = client
    with http.websocket_connect("/api/conversation", headers={"origin": "http://testserver"}) as socket:
        until(socket, "ready")
        socket.send_bytes(b"\0\0" * 2400)
        audio = until(socket, "audio")
        socket.send_bytes(b"\x02\x00")
        assert until(socket, "clear_audio")["id"] == audio["id"]
        socket.send_json({"type": "played", "id": audio["id"], "played_bytes": 960})
        socket.send_json({"type": "stop"})
        with pytest.raises(WebSocketDisconnect):
            while True:
                socket.receive_json()
    truncations = [m for m in model.connection.received if isinstance(m, TruncateOutput)]
    assert [m.audio_end_ms for m in truncations] == [20]


def test_normal_browser_disconnect_leaves_agent_span_healthy(client, capfire):
    http, model = client
    with http.websocket_connect("/api/conversation", headers={"origin": "http://testserver"}) as socket:
        until(socket, "ready")
        socket.close()
    assert model.closed
    roots = [s for s in capfire.exporter.exported_spans if s.attributes.get("gen_ai.operation.name") == "invoke_agent"]
    assert roots and all(s.attributes.get("logfire.level_num", 9) < 17 for s in roots)


def test_cross_origin_page_cannot_start_a_metered_session(client):
    http, model = client
    with pytest.raises(WebSocketDisconnect) as error:
        with http.websocket_connect("/api/conversation", headers={"origin": "https://unrelated.example"}):
            pass
    assert error.value.code == 1008
    assert model.connection is None


async def test_manual_eval_collects_the_realtime_spoken_reply(monkeypatch):
    from audio_demo.evals import run

    model = ScriptedModel()
    monkeypatch.setattr(run, "get_realtime_model", lambda *args: model)
    reply = await run.run_voice("Check account 123")
    assert reply == "Your sample payment was $412.18."
    assert model.closed


@pytest.mark.parametrize("frame", [b"\0", b"\0" * 48002])
def test_invalid_pcm_closes_session(client, frame):
    http, model = client
    with http.websocket_connect("/api/conversation", headers={"origin": "http://testserver"}) as socket:
        until(socket, "ready")
        socket.send_bytes(frame)
        assert socket.receive_json()["type"] == "error"
        with pytest.raises(WebSocketDisconnect) as error:
            socket.receive_json()
        assert error.value.code == 1011
    assert model.closed
