import asyncio
from unittest.mock import AsyncMock

import pytest

from audio_demo.conversation import BrowserPlayback, PlaybackAck


async def test_playback_waits_for_browser_and_uses_partial_position_for_barge_in():
    session = AsyncMock()
    sent = asyncio.Queue()
    playback = BrowserPlayback(session, sent.put)

    async def chunks():
        yield b"\0" * 4800
        yield b"\0" * 2400

    task = asyncio.create_task(playback.stream(chunks()))
    try:
        assert (await asyncio.wait_for(sent.get(), 1))["id"] == 1
        assert sent.empty()  # second chunk cannot run ahead of actual playback
        await playback.interrupt()
        assert await sent.get() == {"type": "clear_audio", "id": 1}
        await playback.acknowledge(PlaybackAck(type="played", id=1, played_bytes=960))
        session.interrupt.assert_awaited_once_with(played_bytes=960)
        assert (await asyncio.wait_for(sent.get(), 1))["id"] == 2
        await playback.interrupt()
        await sent.get()
        await playback.acknowledge(PlaybackAck(type="played", id=2, played_bytes=480))
        # Previous flushed chunk is included in tap coordinates on subsequent turns.
        session.interrupt.assert_awaited_with(played_bytes=5280)
        await asyncio.wait_for(task, 1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_invalid_partial_playback_does_not_advance_the_stream():
    playback = BrowserPlayback(AsyncMock(), AsyncMock())
    playback.chunk_id = 1
    playback.chunk_size = 4800
    playback.pending = asyncio.get_running_loop().create_future()
    with pytest.raises(ValueError, match="Incomplete"):
        await playback.acknowledge(PlaybackAck(type="played", id=1, played_bytes=10))
    assert not playback.pending.done()
