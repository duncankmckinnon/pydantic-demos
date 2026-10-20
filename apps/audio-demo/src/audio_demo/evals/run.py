"""Text-seeded realtime conversations: evaluates transcripts, not microphone quality."""

import asyncio
import os

from pydantic_ai import PartEndEvent, SpeechPart
from pydantic_ai.realtime import RealtimeTurnCompleteEvent

from audio_demo.agent import MODEL_NAME, build_agent
from demo_core.logfire_setup import configure_logfire
from demo_core.models import get_realtime_model
from demo_core.settings import GatewaySettings, LogfireSettings


async def run_voice(prompt: str) -> str:
    agent = build_agent()
    model = get_realtime_model(MODEL_NAME, GatewaySettings())
    replies = []
    async with asyncio.timeout(60):
        async with agent.realtime(model).session() as session:
            await session.send(prompt)
            async for event in session:
                if isinstance(event, PartEndEvent) and isinstance(event.part, SpeechPart):
                    if event.part.speaker == "assistant" and event.part.transcript:
                        replies.append(event.part.transcript)
                if isinstance(event, RealtimeTurnCompleteEvent) and replies:
                    break
    if not replies:
        raise RuntimeError("The realtime model returned no spoken transcript")
    return " ".join(replies)


if __name__ == "__main__":
    configure_logfire(
        "audio-demo-evals",
        send_to_logfire=os.environ.get("LOGFIRE_SEND_TO_LOGFIRE", "true").lower() != "false",
        token=LogfireSettings().token,
    )
    from audio_demo.evals.dataset import voice_dataset

    voice_dataset.evaluate_sync(run_voice, max_concurrency=1).print()
