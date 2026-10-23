# pydantic-demos

Local demo environment for agentic applications built with Pydantic AI, Pydantic AI Harness,
and Logfire. See [AGENTS.md](AGENTS.md) for the repo structure and how to add a new demo.

## Layout

- `packages/demo_core/` — shared library (Gateway model helper, Logfire setup, FastAPI app
  factory, eval-judge template) used by the Python agent demos.
- `apps/<name>/` — one demo application per folder, each with its own `.env`, Dockerfile, and
  README.
- `docker-compose.yml` — runs demos standalone (`docker compose --profile <name> up`) or
  together (`--profile all`). Each service's host port has a default and is overridable via
  a `.env` at the repo root (copy `.env.example` there) — separate from each app's own
  `apps/<name>/.env`, since a host port mapping is resolved before a container starts.

## Demos

- [`audio-demo`](apps/audio-demo/README.md) — real-time, interruptible voice conversations
  with live transcripts, Pydantic AI Gateway, and agent traces in Logfire.
- [`chat`](apps/chat/README.md) — general-purpose chat assistant with web search,
  per-conversation memory, and live Pydantic AI docs lookup.
- [`rx-assistant`](apps/rx-assistant/README.md) — pharmacy prescription assistant grounded
  in a real medication database.
- [`annotation-studio`](apps/annotation-studio/README.md) — review-queue tool for grading
  traces from any Logfire project.
- [`ruby-otel`](apps/ruby-otel/README.md) — RubyLLM travel agent with OpenTelemetry routed
  through a Collector to Logfire.

## Running a demo

Each demo uses the same Docker Compose deployment pattern. See its README for the
environment variables and any one-time setup it requires:

```bash
docker compose --profile <name> up --build -d
```

To run every demo:

```bash
docker compose --profile all up --build -d
```

To stop the Compose project:

```bash
docker compose down
```
