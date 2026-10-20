# Audio demo

A browser version of the existing `Documents/code/audio-demo` voice assistant:
real-time speech through Pydantic AI Gateway, spoken replies, live transcripts for
both speakers, natural interruptions, and the original sample payment-history tool.

## Run

Copy `.env.example` to `.env` in this directory and set this demo's own
`PYDANTIC_AI_GATEWAY_API_KEY` and `LOGFIRE_TOKEN`. An existing audio-demo `.env` can
be reused. No direct OpenAI key or browser token is required.

From the repository root:

```sh
docker compose --profile audio-demo up --build -d
```

Open **http://localhost:8004**, click **Start conversation**, and allow microphone
access. Speak naturally; the assistant replies aloud and both sides appear in the
transcript. Try “Check the payments on account 123.” The payment data is fixed
sample data. Use headphones to avoid speaker feedback. **Mute microphone** keeps
the call connected; **End call** releases the microphone and closes the agent session.

For host development:

```sh
uv sync --all-packages
uv run --package audio-demo uvicorn audio_demo.main:app --reload --port 8004
```

Set `AUDIO_DEMO_PORT` in the **repository root's** `.env` to override Docker's host
port. Microphone access requires localhost or HTTPS and a browser supporting
AudioWorklet (current Chrome, Edge, Firefox, or Safari). Docker does not need
PortAudio or access to a host sound device: the browser owns the microphone/speakers.

## Agent tracing in Logfire

Exactly as in `chat` and `rx-assistant`, startup calls
`demo_core.logfire_setup.configure_logfire("audio-demo", ...)` before constructing
the agent or app. That enables `logfire.instrument_pydantic_ai()`. Pydantic AI's
native realtime instrumentation creates the `audio_agent` run and its model
responses, speech, tool calls/results, and provider usage. The realtime session
is marked `pydantic_ai.realtime = true`. Filter Logfire by service `audio-demo`.

These are **agent traces**, not manually reconstructed WebSocket event logs. The
standard FastAPI instrumentation remains part of the shared app factory, but is
not what provides the conversation trace. Transcripts/tool content are included;
raw PCM is not retained in session history (`audio_retention="transcript_only"`).
Ordinary hang-ups terminate inside the session context so they do not appear as
agent failures; genuine provider/transport errors propagate into the agent span.

The Logfire project comes from this app's `LOGFIRE_TOKEN`. Set
`LOGFIRE_SEND_TO_LOGFIRE=false` to disable export locally. Credentials stay on the
server and `.env` is gitignored. No collector or extra Logfire project is needed.

## Transport

The browser connects to `/api/conversation`. FastAPI relays mono, little-endian
PCM16 to a Pydantic AI realtime session using `gpt-realtime` through Gateway.
The server announces the session's input/output sample rates; the browser's
AudioWorklet captures/resamples microphone audio in 100 ms frames.

Output chunks carry IDs, and the browser acknowledges actual playback. Only one
output chunk is outstanding. On detected speech the browser stops its current
chunk and reports its partial playback position. The relay calls
`session.interrupt(played_bytes=...)` to flush queued output and truncate provider
history to what was heard. Therefore `handle_barge_in` remains disabled: the
session cannot infer browser speaker consumption from a WebSocket send.

The same connection streams incremental, replaceable transcripts and tool status.
The client renders model text with `textContent`. Calls are memory-only, independent
per connection, and end on disconnect; there is no persistent call store. This is
a local demo, with a same-origin WebSocket check, not a public authenticated service.

## Tests and evals

```sh
uv run pytest apps/audio-demo/tests/
node --test apps/audio-demo/tests/*.test.cjs
uv run --package audio-demo python -m audio_demo.evals.run
```

Unit tests never call real models. They use `agent.override(model=TestModel())`
for the tool and a scripted `RealtimeModel` connection for the duplex session
(the ordinary `TestModel` does not implement realtime). Tests inspect actual
captured Pydantic AI spans, transcripts, usage, clean disconnects, audio relay,
and partial-playback truncation.

The manual Pydantic Evals dataset makes real Gateway calls. It seeds the same
realtime agent with text and checks spoken-response transcripts for payment amount,
autopay, and missing-account behavior. These small deterministic checks do not
measure acoustic quality, interruption latency, or end-to-end microphone quality.
The original source's synthetic trace generator and separate claims-classification
experiments are not part of the browser conversation demo.
