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
- [`ruby-otel`](apps/ruby-otel/README.md) — live Ruby travel agent using Pydantic AI Gateway,
  the OpenAI Responses API, Open-Meteo, and OpenStreetMap. Its AI-style traces—including
  captured agent/model requests, responses, tool calls, and token usage—route through a
  dedicated OpenTelemetry Collector to Logfire; the same Collector reports host metrics and
  Docker metrics scoped to the demo container. It is available at
  <http://localhost:8006> by default.

## Running a demo

Most demos follow the same pattern — see each README (linked above) for its exact
required env vars, default port, and any extra one-time setup:

1. `cd apps/<name>`, `cp .env.example .env`, and fill in credentials.
2. Run it either directly on the host (typically
   `uv run --package <name> uvicorn <name>.main:app --reload`) or in Docker from the repo
   root (`docker compose --profile <name> up --build -d`).

The Ruby OpenTelemetry demo is Docker-only and does not belong to the Python `uv`
workspace. It uses two gitignored credential files so the Ruby application receives only
the Gateway key while the Collector receives only the Logfire token:

```bash
cp apps/ruby-otel/.env.example apps/ruby-otel/.env
cp apps/ruby-otel/agent.env.example apps/ruby-otel/agent.env
docker compose --profile ruby-otel up --build
```

After setting the credentials described in the [Ruby demo README](apps/ruby-otel/README.md),
exercise the agent with:

```bash
curl --fail-with-body \
  --header 'Content-Type: application/json' \
  --data '{"destination":"Kyoto, Japan"}' \
  http://localhost:8006/travel
```

`docker compose --profile all up --build -d` runs every demo at once.
