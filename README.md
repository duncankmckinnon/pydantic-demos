# pydantic-demos

Local demo environment for agentic applications built with Pydantic AI, Pydantic AI Harness,
and Logfire. See [AGENTS.md](AGENTS.md) for the repo structure and how to add a new demo.

## Layout

- `packages/demo_core/` — shared library (Gateway model helper, Logfire setup, FastAPI app
  factory, eval-judge template) used by every demo.
- `apps/<name>/` — one demo application per folder, each with its own `.env` and Dockerfile.
- `docker-compose.yml` — runs demos standalone (`docker compose --profile <name> up`) or
  together (`--profile all`).

## Quick start (chat demo)

```bash
cd apps/chat
cp .env.example .env   # fill in PYDANTIC_AI_GATEWAY_API_KEY, LOGFIRE_TOKEN
cd ../..
uv run --package chat uvicorn chat.main:app --reload
```

## Quick start (rx-assistant demo)

```bash
cd apps/rx-assistant
cp .env.example .env   # fill in PYDANTIC_AI_GATEWAY_API_KEY, LOGFIRE_TOKEN
cd ../..
docker compose --profile rx-assistant up -d rx-assistant-db
uv run --package rx-assistant python -m rx_assistant.ingest   # one-time: embeds medicines.csv
uv run --package rx-assistant uvicorn rx_assistant.main:app --reload
```
