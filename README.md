# pydantic-demos

Local demo environment for agentic applications built with Pydantic AI, Pydantic AI Harness,
and Logfire. See [AGENTS.md](AGENTS.md) for the repo structure and how to add a new demo.

## Layout

- `packages/demo_core/` — shared library (Gateway model helper, Logfire setup, FastAPI app
  factory, eval-judge template) used by every demo.
- `apps/<name>/` — one demo application per folder, each with its own `.env`, Dockerfile, and
  README.
- `docker-compose.yml` — runs demos standalone (`docker compose --profile <name> up`) or
  together (`--profile all`). Each service's host port has a default and is overridable via
  a `.env` at the repo root (copy `.env.example` there) — separate from each app's own
  `apps/<name>/.env`, since a host port mapping is resolved before a container starts.

## Demos

- [`chat`](apps/chat/README.md) — general-purpose chat assistant with web search,
  per-conversation memory, and live Pydantic AI docs lookup.
- [`rx-assistant`](apps/rx-assistant/README.md) — pharmacy prescription assistant grounded
  in a real medication database.
- [`annotation-studio`](apps/annotation-studio/README.md) — review-queue tool for grading
  traces from any Logfire project.

## Running a demo

Every demo follows the same pattern — see its own README (linked above) for its exact
required env vars, default port, and any extra one-time setup:

1. `cd apps/<name>`, `cp .env.example .env`, and fill in credentials.
2. Run it either directly on the host (typically
   `uv run --package <name> uvicorn <name>.main:app --reload`) or in Docker from the repo
   root (`docker compose --profile <name> up --build -d`).

`docker compose --profile all up --build -d` runs every demo at once.
