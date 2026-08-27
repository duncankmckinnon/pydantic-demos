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
uv run --package rx-assistant python -m rx_assistant.ingest   # one-time: embeds db-init/medicines.csv.gz
uv run --package rx-assistant uvicorn rx_assistant.main:app --reload
```

Open http://localhost:8000. The dataset (`apps/rx-assistant/db-init/medicines.csv.gz` —
23,939 medications across 141 conditions scraped from netmeds.com) is committed to the repo
gzipped, so no separate download step is needed. Ingestion creates the `vector` extension,
tables, and embeddings the first time it runs; it's not run automatically on app startup, so
rerun it by hand whenever the CSV changes — it's idempotent, truncating and repopulating both
tables each time.

### Running the full stack in Docker

The steps above run only Postgres in a container and the app on the host. To run the app
itself and its Postgres-monitoring OTel Collector in containers too:

```bash
docker compose --profile rx-assistant up -d rx-assistant-db
uv run --package rx-assistant python -m rx_assistant.ingest   # still one-time, from the host
docker compose --profile rx-assistant up -d --build
```

The containerized app is reachable at http://localhost:8002 (mapped from the container's own
port 8000 — note this differs from the host-run workflow above, which serves on 8000
directly). Both the app and the OTel Collector expect a real `LOGFIRE_TOKEN` in `.env`; if you
don't have one, set `LOGFIRE_SEND_TO_LOGFIRE=false` so the app skips Logfire entirely (the
collector container will still start either way — without a token its exports just silently
fail rather than blocking startup).
