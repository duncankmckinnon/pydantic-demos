# Demo Environment Skeleton — Design

Date: 2026-08-25
Status: Approved for implementation planning

## Motivation

This repo will host multiple agentic demo applications, each built with
Pydantic AI (and, where useful, Pydantic AI Harness), instrumented with
Logfire (tracing, resource monitoring, evals), and packaged to run locally via
Docker. Demos are aimed at different customer types, so each needs its own UI,
its own credentials (a Pydantic AI Gateway token/project and a Logfire
token/project), and the ability to be run standalone or chained together with
other demos. The goal of this skeleton is to establish the shared structure —
a common library, credential/config conventions, observability setup, and
Docker packaging — once, so that adding the Nth demo is mostly copying a
pattern rather than re-deriving one.

Everything here targets **local execution only**. There is no production
deployment, secrets manager, or CI concern in scope.

## Architecture Overview

A `uv` workspace monorepo: one shared library package (`demo_core`) that every
demo app depends on as a local editable dependency, plus a folder of
independent FastAPI demo apps, orchestrated locally via one root
`docker-compose.yml`.

```
pydantic-demos/
├── pyproject.toml                 # uv workspace root
├── uv.lock
├── docker-compose.yml              # one service per demo app; profiles group chained sets
├── AGENTS.md                       # repo structure, conventions, how to add a demo
├── .claude/
│   └── skills/
│       └── add-demo/
│           └── SKILL.md            # project skill: scaffolds a new demo per convention
├── packages/
│   └── demo_core/                  # shared library
│       ├── pyproject.toml
│       └── src/demo_core/
│           ├── settings.py         # GatewaySettings, LogfireSettings, AppAuthSettings
│           ├── logfire_setup.py    # configure_logfire(service_name, environment)
│           ├── models.py           # get_model(api_format, model_name, settings) via Gateway
│           └── web.py              # FastAPI app factory: health, auth, instrument_fastapi
└── apps/
    └── chat/                       # first demo
        ├── Dockerfile
        ├── pyproject.toml
        ├── .env.example
        ├── src/chat/
        │   ├── main.py             # FastAPI app + minimal HTML/JS UI
        │   ├── agent.py            # pydantic-ai agent, model-picker over Gateway models
        │   └── evals/
        │       ├── dataset.py
        │       └── run.py
        └── tests/
```

## Shared Core (`demo_core`)

### `settings.py`

Three `pydantic-settings` `BaseSettings` classes, each reading from whichever
app's `.env` is active in that process:

- `GatewaySettings` — `api_key: str` (from `PYDANTIC_AI_GATEWAY_API_KEY`).
- `LogfireSettings` — `token: str`, `project: str | None` (Logfire's standard
  env vars/config resolve most of this; this class exists to make the
  per-app override explicit and typed).
- `AppAuthSettings` — `username: str`, `password: str` for the demo's
  customer-facing basic-auth credential.

### `logfire_setup.py`

```python
def configure_logfire(service_name: str, environment: str = "local") -> None:
    ...
```

Encapsulates the ordering Logfire requires: `logfire.configure(service_name=...,
environment=...)` **then** `logfire.instrument_pydantic_ai()` and
`logfire.instrument_system_metrics()` (the resource-monitoring requirement).
Every app calls this once at startup, before constructing its `Agent` and
before creating its FastAPI app. `logfire.instrument_fastapi(app)` is called
separately by `web.py`'s app factory, since it needs the app instance.

### `models.py`

```python
def get_model(api_format: str, model_name: str, settings: GatewaySettings) -> Model:
    ...
```

Builds a `gateway_provider(api_format, api_key=settings.api_key)` (from
`pydantic_ai.providers.gateway`) and wraps it in the model class matching
`api_format` (e.g. `OpenAIChatModel` for `"openai"`, `AnthropicModel` for
`"anthropic"`). This is the one place Gateway wiring lives, so each demo picks
a model by `(api_format, model_name)` without touching provider construction
directly. Per-demo credential isolation ("different tokens for different
projects") falls directly out of `GatewaySettings` being loaded from that
demo's own `.env` — no additional registry or indirection needed.

### `web.py`

A FastAPI app factory: creates the `FastAPI()` instance, calls
`logfire.instrument_fastapi(app)`, adds a `/health` route, and adds an
`AppAuthSettings`-backed HTTP basic-auth dependency that demos apply to their
routes.

**Explicitly not included in `demo_core`:** an agent-factory abstraction for
harness capabilities, and a shared evals-runner abstraction. With only one
demo so far, there is no second call site to justify either — both are
documented as conventions (see Testing & Evals, and `AGENTS.md`) so the second
demo can copy the pattern, and only then do they move into `demo_core` if
they turn out identical.

## Credentials & Config Management

Each app under `apps/<name>/` has its own `.env` (gitignored) and a checked-in
`.env.example` documenting the required keys:

```
PYDANTIC_AI_GATEWAY_API_KEY=
LOGFIRE_TOKEN=
LOGFIRE_PROJECT=
APP_AUTH_USER=
APP_AUTH_PASSWORD=
```

This gives every demo an independent Gateway project/token and an independent
Logfire project/token, and lets each demo's customer-facing login differ,
without any shared-secrets machinery. `docker-compose.yml` loads each
service's `.env` via that service's own `env_file:` entry.

## Docker & Chaining

One root `docker-compose.yml` defines one service per demo app (build context
`apps/<name>`, `env_file: apps/<name>/.env`, its own port mapping), all on one
shared bridge network so services can reach each other by service name.

"Chaining" is not a distinct mechanism — a chained demo is just another app in
`apps/` whose `.env`/config points at another demo's service name as a base
URL and calls it over HTTP like any external API. Compose `profiles` group
which services start together for a given scenario:

```yaml
services:
  chat:
    build: ./apps/chat
    env_file: ./apps/chat/.env
    ports: ["8001:8000"]
    profiles: ["chat", "all"]
```

`docker compose --profile chat up` runs one demo; `--profile all` brings up
everything. No new shared-core code is required for this — it falls out of
the env-file and workspace-member conventions already defined.

## First Demo: `chat`

- **`agent.py`** — a module-level `Agent` built via `demo_core.models.get_model`
  with a default `(api_format, model_name)`, given an explicit
  `name="chat_agent"` so Logfire traces are labeled. A `MODEL_CHOICES: list[tuple[str, str]]`
  constant (e.g. `[("anthropic", "claude-sonnet-4-6"), ("openai", "gpt-5.2")]`)
  backs the UI's model dropdown. Switching models per request uses
  `agent.override(model=get_model(*choice, settings))` around the run.
- **`main.py`** — built on `demo_core.web`'s app factory. Routes: `GET /`
  serves a minimal server-rendered HTML page (model `<select>`, message box,
  transcript); `POST /api/chat` runs the agent with the selected model and
  returns the reply. Conversation history is kept server-side in memory,
  keyed by a session cookie, holding pydantic-ai message history —
  intentionally not durable across restarts.
- **UI** — plain Jinja2-rendered HTML plus a few lines of vanilla JS calling
  `/api/chat` via `fetch`. No JS framework, no build step.
- **`evals/`** — `dataset.py` defines a small pydantic-evals `Dataset` of
  `Case`s (e.g. "responds sensibly to a greeting", "declines an out-of-scope
  request"); `run.py` executes them against the real agent (real model calls,
  so run manually via `uv run python -m chat.evals.run`, not part of the
  default test suite). Since the agent is already Logfire-instrumented, eval
  runs appear in traces with no extra reporting code.
- **`.env.example`** documents `PYDANTIC_AI_GATEWAY_API_KEY`, `LOGFIRE_TOKEN`,
  `LOGFIRE_PROJECT`, `APP_AUTH_USER`, `APP_AUTH_PASSWORD`.

## Testing & Evals Conventions

- **Unit tests** (`apps/<name>/tests/`): use `TestModel`/`FunctionModel` via
  `agent.override(model=...)` for deterministic, no-network tests of routes
  and agent logic. Run with `uv run pytest`, either per-app or from the
  workspace root.
- **Evals** (`apps/<name>/src/<name>/evals/`): real-model-call `Dataset`s of
  `Case`s, run manually/on demand, not part of the default test suite. Traced
  automatically via the same Logfire instrumentation as the app itself.
- No shared `demo_core` test/eval helpers yet (see rationale above); the
  pattern is documented in `AGENTS.md` for the next demo to copy.

## Repo-Level Agent Guidance

- **`AGENTS.md`** (root) documents: the repo's purpose and constraints (local
  execution, multiple customer-targeted demos, per-app Gateway/Logfire
  credentials); the `packages/demo_core` vs `apps/<name>` split and each
  `demo_core` module's contract; the per-app `.env` convention; the
  docker-compose service+profile pattern; and the testing/evals convention
  above. This is the single source of truth for "how this repo fits
  together" — no separate `docs/adding-a-new-demo.md`.
- **`.claude/skills/add-demo/SKILL.md`** — a project skill for scaffolding a
  new demo. It states the repo's motivation and constraints so a fresh
  session doesn't have to re-derive them, gives the concrete scaffolding
  checklist (create `apps/<name>`, its `pyproject.toml`/`.env.example`/
  `Dockerfile`, add it to the uv workspace members and to
  `docker-compose.yml` with a profile), and directs the agent to invoke
  `ai:building-pydantic-ai-agents` for agent/tool construction,
  `pydantic-ai-harness:pydantic-ai-harness` if the demo needs sandboxed tool
  orchestration or sub-agents, and `logfire:logfire-instrumentation` for
  anything beyond the standard `configure_logfire()` call. Authoring this
  file follows `superpowers:writing-skills` conventions at implementation
  time.

## Out of Scope (this skeleton)

- Only one real demo (`chat`) is built now; no scaffolding generator beyond
  the `add-demo` skill and `AGENTS.md` convention.
- No streaming responses in the chat UI (plain request/response); streaming
  via `run_stream` is a straightforward later addition.
- No persistent chat history (in-memory per session, lost on restart).
- No production auth/secrets management — basic-auth plus `.env` files only,
  since this is local-only by design.
- No CI wiring.

## Implementation Notes / Verify-at-build-time

- `demo_core/models.py`'s exact model-class dispatch per `api_format`
  (`OpenAIChatModel`, `AnthropicModel`, etc.) should be checked against the
  current Pydantic AI Gateway docs/SDK during implementation — the mapping
  above reflects the docs as of this design's writing
  (https://pydantic.dev/docs/ai/overview/gateway/).
- The `ai:building-pydantic-ai-agents`, `pydantic-ai-harness:pydantic-ai-harness`,
  and `logfire:logfire-instrumentation` skills should be consulted again
  during implementation for exact call signatures, rather than relying solely
  on this design doc.
