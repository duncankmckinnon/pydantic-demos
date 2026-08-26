# rx-assistant Demo — Design

Date: 2026-08-26
Status: Approved for implementation planning

## Motivation

The repo's demo skeleton (see `2026-08-25-demo-skeleton-design.md`) established the shared
`demo_core` library and the pattern for adding a new `apps/<name>` demo, proven out by the
`chat` demo. This spec adds the second demo, `rx-assistant`: a medical Q&A assistant backed
by a vector database of medications and conditions sourced from the repo's `medicines.csv`
(23,939 rows scraped from netmeds.com, covering 141 distinct disease/condition categories).

This is the first demo in the repo to need retrieval over a dataset, so it also introduces
the repo's first vector database and its first non-Gateway-routed model call (local text
embeddings). Both are scoped entirely to `rx-assistant` — nothing here is promoted into
`demo_core`, per `AGENTS.md`'s rule of copying a pattern once before generalizing it.

As with `chat`, everything here targets **local execution only** via Docker Compose. This
is a demo over a public, non-curated dataset — it is explicitly not medical advice, and the
agent's instructions and the UI both say so.

## Data Model

Inspection of `medicines.csv` shows: `disease_name` takes 141 distinct values (each
formatted like `"ADHD (7)"`, where the trailing count is the number of medication rows
scraped under that disease); `med_name` is unique across all 23,939 rows. So the CSV is
effectively one row per medication, each tagged with the one disease category it was
scraped under.

This maps onto two partitioned tables in Postgres, each with its own `vector(384)` column
(384 = the output dimension of the `all-MiniLM-L6-v2` sentence-transformers model) and a
cosine-distance index:

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE conditions (
    id          SERIAL PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,      -- disease_name with the trailing " (N)" stripped
    embedding   vector(384) NOT NULL
);
CREATE INDEX ON conditions USING hnsw (embedding vector_cosine_ops);

CREATE TABLE medications (
    id                       SERIAL PRIMARY KEY,
    condition_id             INTEGER NOT NULL REFERENCES conditions(id),
    med_name                 TEXT NOT NULL,
    med_url                  TEXT,
    generic_name             TEXT,
    drug_content             TEXT,
    drug_variant             TEXT,
    drug_manufacturer        TEXT,
    drug_manufacturer_origin TEXT,
    price                    TEXT,
    final_price              TEXT,
    prescription_required    TEXT,
    embedding                vector(384) NOT NULL
);
CREATE INDEX ON medications USING hnsw (embedding vector_cosine_ops);
```

- `conditions.embedding` is computed from the cleaned condition name alone.
- `medications.embedding` is computed from `med_name + generic_name + drug_content`, so
  similarity search matches on what the medication treats and contains, not just its brand
  name.
- Price, prescription, and manufacturer fields are stored as plain columns (not embedded)
  and returned to the agent as structured metadata alongside similarity matches.
- `img_urls` and the `*_url` scrape artifacts beyond `med_url` are dropped at ingestion —
  they're presentational scrape leftovers with no value to a text Q&A agent.

## Ingestion

A one-time, manually-run CLI script, `python -m rx_assistant.ingest` (parallel to how evals
are run manually via `python -m <name>.evals.run`, not part of the default test suite):

1. Resolve `medicines.csv` from the repo root (`Path(__file__).resolve().parents[4] /
   "medicines.csv"`, mirroring how `chat/__init__.py` resolves its `.env` path relative to
   the package file rather than CWD).
2. Connect to Postgres via `asyncpg` using `DatabaseSettings().database_url`; run the DDL
   above with `CREATE TABLE IF NOT EXISTS` (idempotent).
3. `TRUNCATE conditions, medications RESTART IDENTITY CASCADE` so reruns don't duplicate
   data.
4. Load `sentence-transformers`' `all-MiniLM-L6-v2` once. Encode the 141 distinct cleaned
   condition names in a single batch; insert into `conditions`, building a `name -> id` map.
5. Encode medication embedding text in batches (e.g. 256 rows at a time) and bulk-insert
   into `medications` with the corresponding `condition_id`.

The script is safe to re-run whenever `medicines.csv` changes; it is not invoked
automatically by the app (matching the "one-time build step" decision — app startup only
connects to an already-populated database).

## Agent & Tools

`rx_assistant/agent.py` builds a Pydantic AI `Agent` with `deps_type=Deps`, where `Deps`
bundles an `asyncpg.Pool` and the loaded `SentenceTransformer` instance (constructed once,
in the FastAPI lifespan handler — never at import time, so importing the module never
touches a real database or downloads a model).

Two tools, both embedding their query text with the same local model and querying via the
pool from `ctx.deps`:

- `search_conditions(ctx, query: str, limit: int = 5) -> list[ConditionMatch]` — cosine
  similarity search over `conditions`.
- `search_medications(ctx, query: str, condition: str | None = None, limit: int = 5) ->
  list[MedicationMatch]` — cosine similarity search over `medications`, optionally filtered
  to rows whose `condition_id` matches a `conditions.name ILIKE '%' || condition || '%'`
  lookup (case-insensitive substring match, since the agent may pass a phrase like
  "attention deficit" rather than the exact stored name "ADHD"). If no condition row
  matches, the tool ignores the filter and searches all medications rather than erroring,
  so a slightly-off condition guess still returns useful results.

System instructions direct the agent to use these tools before answering questions about
conditions or medications, to cite the specific medications/prices it retrieved, and to
state plainly that this is demo data and not medical advice — recommending the user consult
a healthcare professional for real decisions.

## API / UI

Mirrors `chat`'s structure directly:

- `rx_assistant/main.py` — `create_rx_app()` factory: calls `configure_logfire("rx-assistant",
  ...)`, builds the FastAPI app via `demo_core.web.create_app`, registers a lifespan handler
  that opens/closes the `asyncpg` pool and loads the embedding model, serves a Jinja2 chat
  page at `/`, and a `POST /api/chat` endpoint with the same in-memory per-session history
  pattern as `chat` (unsynchronized dict, single-user local demo only).
- One model choice list (`MODEL_CHOICES`), same shape as `chat`'s, for the conversational
  model routed through the Gateway — embeddings are local and separate from this.
- The chat template carries a visible disclaimer: dataset is for demo purposes only, not
  medical advice.

## Settings

A new `rx_assistant/settings.py`, local to this app (not added to `demo_core` — no second
demo needs a database yet):

```python
class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)
    database_url: str = Field(validation_alias="DATABASE_URL")
```

`.env.example` adds `DATABASE_URL` alongside the existing `PYDANTIC_AI_GATEWAY_API_KEY` and
`LOGFIRE_TOKEN`, defaulting to the host-facing address
(`postgresql://rx_assistant:rx_assistant@localhost:5433/rx_assistant`) so a developer running
`uv run --package rx-assistant python -m rx_assistant.ingest` from the host, against the
Compose-managed Postgres, works without edits.

## Docker Compose

Three new services, all under `profiles: ["rx-assistant", "all"]`:

```yaml
rx-assistant-db:
  image: pgvector/pgvector:pg16
  environment:
    POSTGRES_USER: rx_assistant
    POSTGRES_PASSWORD: rx_assistant
    POSTGRES_DB: rx_assistant
  env_file: apps/rx-assistant/.env   # supplies POSTGRESQL_USERNAME/PASSWORD to the init script below
  volumes:
    - rx_assistant_db_data:/var/lib/postgresql/data
    - ./apps/rx-assistant/db-init:/docker-entrypoint-initdb.d:ro
  ports:
    - "5433:5432"          # host access for local `uv run` ingestion/dev
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U rx_assistant"]
    interval: 5s
    timeout: 5s
    retries: 5
  profiles: ["rx-assistant", "all"]

rx-assistant:
  build:
    context: .
    dockerfile: apps/rx-assistant/Dockerfile
  env_file: apps/rx-assistant/.env
  environment:
    DATABASE_URL: postgresql://rx_assistant:rx_assistant@rx-assistant-db:5432/rx_assistant
  depends_on:
    rx-assistant-db:
      condition: service_healthy
  ports:
    - "8002:8000"
  profiles: ["rx-assistant", "all"]

rx-assistant-otel-collector:
  image: otel/opentelemetry-collector-contrib:0.116.1
  volumes:
    - ./apps/rx-assistant/otel-collector-config.yaml:/etc/otelcol-contrib/config.yaml:ro
  env_file: apps/rx-assistant/.env
  depends_on:
    rx-assistant-db:
      condition: service_healthy
  profiles: ["rx-assistant", "all"]

volumes:
  rx_assistant_db_data:
```

The `environment:` block on `rx-assistant` intentionally overrides the host-oriented
`DATABASE_URL` from `.env` (loaded via `env_file:`) with the in-container hostname —
Compose's `environment:` takes precedence over `env_file:` for the same key. Ingestion run
inside the container (e.g. `docker compose --profile rx-assistant exec rx-assistant python
-m rx_assistant.ingest`) picks up the in-container URL automatically; ingestion run from the
host uses the `.env` value against the published `5433` port.

## Postgres Monitoring (Logfire, beta)

`rx-assistant-db`'s metrics (locks, deadlocks, sequential scans, cache hit ratio, temp
files/IO, WAL delay, bgwriter activity, index size) are shipped to this demo's own Logfire
project via an OpenTelemetry Collector, following Logfire's current Postgres-monitoring
integration guide (a beta offering).

**Monitoring role:** a dedicated, least-privilege login, created once via a Postgres
`docker-entrypoint-initdb.d` init script (`apps/rx-assistant/db-init/01-create-monitor-role.sh`,
executable, since a `.sql` file in that directory can't expand environment variables) that
grants the built-in `pg_monitor` role — read-only access to the statistics views the
collector's `postgresql` receiver reads, nothing else:

```sql
CREATE USER "$POSTGRESQL_USERNAME" WITH PASSWORD '$POSTGRESQL_PASSWORD';
GRANT pg_monitor TO "$POSTGRESQL_USERNAME";
```

**Collector config** (`apps/rx-assistant/otel-collector-config.yaml`, mounted read-only into
the `rx-assistant-otel-collector` service): the `postgresql` receiver only ships in the
**contrib** Collector distribution, hence `otel/opentelemetry-collector-contrib` rather than
the core image. Two deltas from the stock integration snippet, both required for this local
Compose topology rather than a bare-metal host:

- `endpoint: rx-assistant-db:5432` (the Compose service hostname, not `localhost` — the
  collector runs in its own container).
- `tls: insecure: true` uncommented — the `pgvector/pgvector:pg16` image has no TLS
  certificate configured out of the box, so the receiver's TLS-by-default connection would
  otherwise fail.

`service.instance.id` is set to the fixed string `"rx-assistant-db-local"` — this demo runs
a single, non-scaled Postgres instance, so a static value already satisfies "stable across
collector restarts."

```yaml
receivers:
  postgresql:
    endpoint: rx-assistant-db:5432
    username: ${env:POSTGRESQL_USERNAME}
    password: ${env:POSTGRESQL_PASSWORD}
    databases: []  # empty = all non-template databases
    collection_interval: 10s
    tls:
      insecure: true
    metrics:
      postgresql.database.locks: { enabled: true }
      postgresql.deadlocks: { enabled: true }
      postgresql.sequential_scans: { enabled: true }
      postgresql.blks_hit: { enabled: true }
      postgresql.blks_read: { enabled: true }
      postgresql.temp_files: { enabled: true }
      postgresql.temp.io: { enabled: true }
      postgresql.wal.delay: { enabled: true }
      postgresql.bgwriter.maxwritten: { enabled: true }
      postgresql.bgwriter.buffers.allocated: { enabled: true }
      postgresql.index.size: { enabled: true }
processors:
  resource/postgresql:
    attributes:
      - { key: service.name, value: postgresql, action: upsert }
      - { key: service.namespace, value: infrastructure, action: upsert }
      - { key: service.instance.id, value: "rx-assistant-db-local", action: upsert }
  batch: {}
exporters:
  otlphttp/logfire:
    endpoint: ${env:LOGFIRE_INGEST_URL}
    headers:
      Authorization: Bearer ${env:LOGFIRE_TOKEN}
service:
  pipelines:
    metrics/postgresql:
      receivers: [postgresql]
      processors: [resource/postgresql, batch]
      exporters: [otlphttp/logfire]
```

`.env.example` adds, alongside the existing keys:

```
POSTGRESQL_USERNAME=logfire_monitor
POSTGRESQL_PASSWORD=logfire_monitor
LOGFIRE_INGEST_URL=https://logfire-us.pydantic.dev
```

These are local-only, non-secret demo credentials (repo-wide convention — no secrets
manager); `LOGFIRE_INGEST_URL` defaults to Logfire's US region and reuses the app's own
`LOGFIRE_TOKEN`, so Postgres metrics land in the same Logfire project as the app's traces.
EU accounts or self-hosted Logfire need to override `LOGFIRE_INGEST_URL` in their own
`.env`.

## Dependencies (`apps/rx-assistant/pyproject.toml`)

`demo-core`, `pydantic-ai`, `pydantic-evals`, `fastapi`, `uvicorn[standard]`, `jinja2`,
`python-dotenv`, `asyncpg`, `pgvector` (for the asyncpg vector type codec), `sentence-transformers`.

## Testing

Unit tests follow `chat`'s pattern: `TestModel`/`FunctionModel` via `agent.override(model=...)`,
a `conftest.py` forcing dummy `PYDANTIC_AI_GATEWAY_API_KEY`, `LOGFIRE_TOKEN`,
`LOGFIRE_SEND_TO_LOGFIRE=false`, and a dummy `DATABASE_URL` (never dialed — see below) at
module level. No `tests/__init__.py`.

Because `create_rx_app()` only opens the `asyncpg` pool inside the FastAPI lifespan handler
(not at import or plain construction time), tests can construct the app and monkeypatch the
tool-level query functions in `rx_assistant.agent` (e.g. `_query_conditions`,
`_query_medications`) to return canned `ConditionMatch`/`MedicationMatch` data, the same way
`chat`'s tests monkeypatch `get_model` — so the default `pytest` suite never opens a real
database connection or loads the real embedding model. The ingestion script and any
real-Postgres smoke test are run manually, like evals, and are out of scope for the default
suite.

## Out of Scope

- No production deployment, auth, or secrets manager (repo-wide convention).
- No re-ranking, hybrid search, or chunking strategy beyond one row = one embedded
  record — the dataset is small and short-text enough that this is unnecessary.
- No automatic re-ingestion on `medicines.csv` changes; the developer reruns the script.
- No promotion of vector-DB, local-embedding, or Postgres-monitoring helpers into
  `demo_core` — this is the first demo to need them; `AGENTS.md`'s rule is to copy the
  pattern once before generalizing it.
