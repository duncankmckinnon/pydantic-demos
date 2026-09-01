# Rx Assistant

A pharmacy prescription assistant demo built with Pydantic AI: grounds every medication it
mentions in a real pgvector-backed database (2,966 medications across 49 conditions, scraped
from drugs.com) rather than its own general knowledge, citing name, generic name, drug class,
prescription status, and controlled-substance schedule.

## Configure

```bash
cd apps/rx-assistant
cp .env.example .env   # fill in PYDANTIC_AI_GATEWAY_API_KEY, LOGFIRE_TOKEN
```

`.env.example` also has Postgres-monitoring settings (`POSTGRESQL_USERNAME` /
`POSTGRESQL_PASSWORD` / `LOGFIRE_INGEST_URL`) for the OTel Collector below — these have
working defaults and don't need to change for local use.

## Run on the host

```bash
cd ../..   # repo root
docker compose --profile rx-assistant up -d rx-assistant-db
uv run --package rx-assistant python -m rx_assistant.ingest   # one-time: embeds db-init/drugs.csv.gz
uv run --package rx-assistant uvicorn rx_assistant.main:app --reload
```

Open http://localhost:8000. The dataset (`db-init/drugs.csv.gz`) is committed to the repo
gzipped, so no separate download step is needed. Ingestion creates the `vector` extension,
tables, and embeddings the first time it runs; it's not run automatically on app startup, so
rerun it by hand whenever the CSV changes — it's idempotent, truncating and repopulating both
tables each time.

Only Postgres runs in a container in this workflow — the app itself runs on the host, so it
reads `.env`'s `DATABASE_URL` directly, which defaults to `localhost:5433`. If you've
overridden `RX_ASSISTANT_DB_PORT` (see the repo root's `.env.example`), update `DATABASE_URL`
here to match — it's a separate value, not derived from that override.

## Run in Docker

To run the app itself and its Postgres-monitoring OTel Collector in containers too, instead
of just Postgres:

```bash
docker compose --profile rx-assistant up -d rx-assistant-db
uv run --package rx-assistant python -m rx_assistant.ingest   # still one-time, from the host
docker compose --profile rx-assistant up -d --build
```

Open http://localhost:8002 by default (mapped from the container's own port 8000 — note this
differs from the host-run workflow above, which serves on 8000 directly). Override the host
ports with `RX_ASSISTANT_PORT` / `RX_ASSISTANT_DB_PORT` in a `.env` at the repo root (copy the
root's `.env.example`) if you want different ones; in this workflow the container gets
`DATABASE_URL` from `docker-compose.yml` directly; not from `.env`, so it always resolves the
DB correctly regardless of any host-port override.

Both the app and the OTel Collector expect a real `LOGFIRE_TOKEN` in `.env`; if you don't have
one, set `LOGFIRE_SEND_TO_LOGFIRE=false` so the app skips Logfire entirely (the collector
container will still start either way — without a token its exports just silently fail rather
than blocking startup).
