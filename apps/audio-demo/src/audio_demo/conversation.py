"""Browser transport for a Pydantic AI realtime session.

The session itself owns all agent/model/tool instrumentation. Browser acknowledgements
pace stream_audio() at actual speaker speed; a WebSocket send is not playback.
"""

import asyncio
import base64
from collections.abc import Awaitable, Callable
from typing import Annotated, Literal

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, TypeAdapter
from pydantic_ai import FunctionToolCallEvent, FunctionToolResultEvent
from pydantic_ai.realtime import (
    RealtimeInputSpeechEndEvent,
    RealtimeInputSpeechStartEvent,
    RealtimeInputTranscriptionErrorEvent,
    RealtimeSession,
    RealtimeSessionErrorEvent,
)


class Stop(BaseModel):
    type: Literal["stop"]


class PlaybackAck(BaseModel):
    type: Literal["played"]
    id: int = Field(ge=1, strict=True)
    played_bytes: int = Field(ge=0, strict=True)


CONTROL = TypeAdapter(Annotated[Stop | PlaybackAck, Field(discriminator="type")])
MAX_AUDIO_BYTES = 48_000  # at most one second of 24 kHz mono PCM16
Send = Callable[[dict], Awaitable[None]]


class BrowserPlayback:
    """One outstanding PCM chunk, acknowledged after playback or a barge-in flush."""

    def __init__(self, session: RealtimeSession, send: Send):
        self.session = session
        self.send = send
        self.lock = asyncio.Lock()
        self.pending: asyncio.Future | None = None
        self.chunk_id = 0
        self.chunk_size = 0
        self.delivered_bytes = 0
        self.interrupt_requested = False

    async def stream(self, audio) -> None:
        async for chunk in audio:
            async with self.lock:
                self.chunk_id += 1
                self.chunk_size = len(chunk)
                self.pending = asyncio.get_running_loop().create_future()
                pending = self.pending
                await self.send({
                    "type": "audio", "id": self.chunk_id,
                    "data": base64.b64encode(chunk).decode("ascii"),
                })
            # A suspended/closed browser must not keep spending on a voice session.
            await asyncio.wait_for(pending, timeout=20)

    async def interrupt(self) -> None:
        async with self.lock:
            if self.pending is not None:
                self.interrupt_requested = True
                await self.send({"type": "clear_audio", "id": self.chunk_id})
            else:
                await self.session.interrupt(played_bytes=self.delivered_bytes)

    async def acknowledge(self, ack: PlaybackAck) -> None:
        async with self.lock:
            if self.pending is None or ack.id != self.chunk_id:
                return  # a late acknowledgement of a chunk already settled
            if ack.played_bytes > self.chunk_size or ack.played_bytes % 2:
                raise ValueError("Invalid playback position")
            if self.interrupt_requested:
                # Pass the partial chunk position BEFORE advancing over the flushed
                # chunk. Later positions must still include that delivered chunk:
                # the session's own tap already yielded it and cannot count its drop.
                await self.session.interrupt(
                    played_bytes=self.delivered_bytes + ack.played_bytes
                )
                self.interrupt_requested = False
            elif ack.played_bytes != self.chunk_size:
                raise ValueError("Incomplete playback acknowledgement")
            self.delivered_bytes += self.chunk_size
            if not self.pending.done():
                self.pending.set_result(None)
            self.pending = None


async def relay_conversation(socket: WebSocket, session: RealtimeSession) -> None:
    send_lock = asyncio.Lock()

    async def send(event: dict) -> None:
        async with send_lock:
            await socket.send_json(event)

    playback = BrowserPlayback(session, send)
    # Subscribe before starting any tasks so the first audio/transcript cannot be lost.
    audio = session.stream_audio()
    transcripts = session.stream_transcripts(delta=True)

    async def receive() -> None:
        while True:
            message = await socket.receive()
            if message["type"] == "websocket.disconnect":
                return
            if (data := message.get("bytes")) is not None:
                if not data or len(data) > MAX_AUDIO_BYTES or len(data) % 2:
                    raise ValueError("Expected mono PCM16 audio, at most one second per frame")
                await session.send_audio(data)
            else:
                control = CONTROL.validate_json(message.get("text", ""))
                if isinstance(control, Stop):
                    return
                await playback.acknowledge(control)

    async def captions() -> None:
        async for update in transcripts:
            await send({
                "type": "transcript", "id": update.index, "speaker": update.speaker,
                "text": update.transcript,
            })

    async def events() -> None:
        async for event in session:
            if isinstance(event, RealtimeInputSpeechStartEvent):
                await playback.interrupt()
                await send({"type": "status", "state": "listening"})
            elif isinstance(event, RealtimeInputSpeechEndEvent):
                await send({"type": "status", "state": "thinking"})
            elif isinstance(event, FunctionToolCallEvent):
                await send({"type": "tool", "name": event.part.tool_name, "state": "calling"})
            elif isinstance(event, FunctionToolResultEvent):
                await send({"type": "tool", "name": event.part.tool_name, "state": "complete"})
            elif isinstance(event, (RealtimeSessionErrorEvent, RealtimeInputTranscriptionErrorEvent)):
                await send({"type": "error", "message": "The voice provider reported an error. See the agent trace in Logfire."})
                if isinstance(event, RealtimeSessionErrorEvent) and not event.recoverable:
                    raise RuntimeError("Realtime provider disconnected")

    tasks = []
    try:
        await send({
            "type": "ready", "input_sample_rate": session.audio_input_sample_rate,
            "output_sample_rate": session.audio_output_sample_rate,
        })
        tasks = [asyncio.create_task(coro) for coro in (
            receive(), playback.stream(audio), captions(), events(),
        )]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()  # propagate genuine errors into the agent's session span
    except WebSocketDisconnect:
        pass  # ordinary hang-up is handled INSIDE the agent's session context
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await audio.aclose()
        await transcripts.aclose()
