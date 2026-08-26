# Demo Environment Skeleton — Design

Date: 2026-08-25
Status: Approved for implementation planning

## Motivation

This repo will host multiple agentic demo applications, each built with
Pydantic AI (and, where useful, Pydantic AI Harness), instrumented with
Logfire (tracing, resource monitoring, evals), and packaged to run locally via
Docker. Demos are aimed at different customer types, so each needs its own UI
and its own credentials (a Pydantic AI Gateway token/project and a Logfire
token/project), and the ability to be run standalone or chained together with
other demos. The goal of this skeleton is to establish the shared structure —
a common library, credential/config conventions, observability setup, and
Docker packaging — once, so that adding the Nth demo is mostly copying a
pattern rather than re-deriving one.

Everything here targets **local execution only**. There is no production
deployment, secrets manager, or CI concern in scope. There is also no
customer-facing login/auth in scope right now — an interesting idea for
later, but nothing in this skeleton needs it yet, so it isn't built.

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
│           ├── settings.py         # GatewaySettings, LogfireSettings
│           ├── logfire_setup.py    # configure_logfire(service_name, environment)
│           ├── models.py           # get_model(api_format, model_name, settings) via Gateway
│           ├── web.py              # FastAPI app factory: health, standard error handling
│           └── evals.py            # HarnessJudge: evaluator template built on an Agent
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

`pydantic-settings` `BaseSettings` classes, each reading from whichever app's
`.env` is active in that process:

- `GatewaySettings` — `api_key: str` (from `PYDANTIC_AI_GATEWAY_API_KEY`).
- `LogfireSettings` — `token: str`, `project: str | None`, making the
  per-app Logfire override explicit and typed.

### `logfire_setup.py`

```python
def configure_logfire(service_name: str, environment: str = "local") -> None:
    ...
```

Encapsulates the ordering Logfire requires: `logfire.configure(service_name=...,
environment=...)` **then** `logfire.instrument_pydantic_ai()` and
`logfire.instrument_system_metrics()`. Every app calls this once at startup,
before constructing its `Agent` and before creating its FastAPI app.
`logfire.instrument_fastapi(app)` is called separately by `web.py`'s app
factory, since it needs the app instance.

This instrumentation pair is deliberately the *only* resource-monitoring
mechanism in the skeleton: `instrument_pydantic_ai()` already captures token
usage and cost per model call inside the agent-run spans, and
`instrument_system_metrics()` already captures host/container CPU, memory,
etc. A separate custom-metrics helper would duplicate data Logfire already
records, so none is built.

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
`logfire.instrument_fastapi(app)`, adds a `/health` route, and registers a
standard exception handler so every demo behaves the same way on an
unhandled error: log it via `logfire.exception(...)` and return a consistent
JSON error shape (e.g. `{"error": "..."}` with an appropriate status code)
instead of each demo growing its own ad hoc error handling.

No shared UI template lives here. Demos may need meaningfully different
frontends as they grow in complexity (some may stay plain HTML/JS, others may
eventually want a real JS framework), so templating a shared "look" now would
constrain that choice for no proven benefit. Each demo owns its UI outright.

### `evals.py`

A reusable evaluator template for the "LLM/agent as judge" pattern, since
every demo will need to evaluate its agent's behavior and most of that
judgment logic looks the same shape:

```python
from dataclasses import dataclass

from pydantic_ai import Agent
from pydantic_evals.evaluators import Evaluator, EvaluatorContext


@dataclass
class HarnessJudge(Evaluator):
    """Scores a case's output by delegating judgment to an Agent.

    The judge agent can be a plain Agent for straightforward rubrics, or a
    pydantic-ai-harness-equipped Agent (e.g. with Shell/CodeMode to actually
    execute and check generated code, or SubAgents to decompose a complex
    rubric) when the judgment itself requires more than a single model call.
    """

    agent: Agent
    rubric: str

    async def evaluate(self, ctx: EvaluatorContext) -> float:
        result = await self.agent.run(
            f"{self.rubric}\n\nOutput to judge:\n{ctx.output}"
        )
        return result.output
```

`demo_core` does not depend on `pydantic-ai-harness` itself — whether the
judge `Agent` passed in is harness-equipped is entirely up to the demo that
constructs it. This class is the shared template; each demo's `evals/`
defines its own rubric and judge agent and plugs them in. The `chat` demo's
eval dataset includes one case using `HarnessJudge` with a plain (non-harness)
judge agent, to establish the pattern; a later demo with more complex outputs
to verify (e.g. generated code) is where equipping the judge agent with
harness capabilities actually pays off.

## Credentials & Config Management

Each app under `apps/<name>/` has its own `.env` (gitignored) and a checked-in
`.env.example` documenting the required keys:

```
PYDANTIC_AI_GATEWAY_API_KEY=
LOGFIRE_TOKEN=
LOGFIRE_PROJECT=
```

This gives every demo an independent Gateway project/token and an independent
Logfire project/token without any shared-secrets machinery.
`docker-compose.yml` loads each service's `.env` via that service's own
`env_file:` entry.

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
- **`main.py`** — built on `demo_core.web`'s app factory (health check and
  standard error handling already wired). Routes: `GET /` serves a minimal
  server-rendered HTML page (model `<select>`, message box, transcript);
  `POST /api/chat` runs the agent with the selected model and returns the
  reply. Conversation history is kept server-side in memory, keyed by a
  session cookie, holding pydantic-ai message history — intentionally not
  durable across restarts.
- **UI** — plain Jinja2-rendered HTML plus a few lines of vanilla JS calling
  `/api/chat` via `fetch`. No JS framework, no build step for this first
  demo; a later demo is free to choose differently.
- **`evals/`** — `dataset.py` defines a small pydantic-evals `Dataset` of
  `Case`s (e.g. "responds sensibly to a greeting", "declines an out-of-scope
  request"), including one case scored with `demo_core.evals.HarnessJudge`;
  `run.py` executes them against the real agent (real model calls, so run
  manually via `uv run python -m chat.evals.run`, not part of the default
  test suite). Since the agent is already Logfire-instrumented, eval runs
  appear in traces with no extra reporting code.
- **`.env.example`** documents `PYDANTIC_AI_GATEWAY_API_KEY`, `LOGFIRE_TOKEN`,
  `LOGFIRE_PROJECT`.

## Testing & Evals Conventions

- **Unit tests** (`apps/<name>/tests/`): use `TestModel`/`FunctionModel` via
  `agent.override(model=...)` for deterministic, no-network tests of routes
  and agent logic. Run with `uv run pytest`, either per-app or from the
  workspace root.
- **Evals** (`apps/<name>/src/<name>/evals/`): real-model-call `Dataset`s of
  `Case`s, run manually/on demand, not part of the default test suite, scored
  with a mix of built-in `pydantic_evals` evaluators and `demo_core.evals.HarnessJudge`
  where a judge agent is the right tool. Traced automatically via the same
  Logfire instrumentation as the app itself.
- No shared `demo_core` code for agent-construction defaults (e.g. harness
  capability bundles) yet — with only one demo so far there's no second call
  site to justify that abstraction. `AGENTS.md` documents the convention so
  the next demo can copy it.

## Repo-Level Agent Guidance

- **`AGENTS.md`** (root) documents: the repo's purpose and constraints (local
  execution, multiple customer-targeted demos, per-app Gateway/Logfire
  credentials, no customer-facing auth yet); the `packages/demo_core` vs
  `apps/<name>` split and each `demo_core` module's contract; the per-app
  `.env` convention; the docker-compose service+profile pattern; and the
  testing/evals convention above. This is the single source of truth for
  "how this repo fits together."
- **`.claude/skills/add-demo/SKILL.md`** — a project skill for scaffolding a
  new demo. It states the repo's motivation and constraints so a fresh
  session doesn't have to re-derive them, gives the concrete scaffolding
  checklist (create `apps/<name>`, its `pyproject.toml`/`.env.example`/
  `Dockerfile`, add it to the uv workspace members and to
  `docker-compose.yml` with a profile), and directs the agent to invoke
  `ai:building-pydantic-ai-agents` for agent/tool construction,
  `pydantic-ai-harness:pydantic-ai-harness` if the demo needs sandboxed tool
  orchestration or sub-agents (including for a `HarnessJudge`-based
  evaluator), and `logfire:logfire-instrumentation` for anything beyond the
  standard `configure_logfire()` call. Authoring this file follows
  `superpowers:writing-skills` conventions at implementation time.

## Out of Scope (this skeleton)

- Only one real demo (`chat`) is built now; no scaffolding generator beyond
  the `add-demo` skill and `AGENTS.md` convention.
- No streaming responses in the chat UI (plain request/response); streaming
  via `run_stream` is a straightforward later addition.
- No persistent chat history (in-memory per session, lost on restart).
- No customer-facing auth/login of any kind — local-only by design, and
  nothing in scope needs it yet.
- No shared UI template/framework — each demo owns its frontend, since
  demos may need different levels of UI complexity.
- No CI wiring.

## Implementation Notes / Verify-at-build-time

- `demo_core/models.py`'s exact model-class dispatch per `api_format`
  (`OpenAIChatModel`, `AnthropicModel`, etc.) should be checked against the
  current Pydantic AI Gateway docs/SDK during implementation — the mapping
  above reflects the docs as of this design's writing
  (https://pydantic.dev/docs/ai/overview/gateway/).
- `demo_core/evals.py`'s `HarnessJudge` return type/signature should be
  checked against the current `pydantic_evals.evaluators.Evaluator` API
  during implementation (confirmed shape as of this design:
  `evaluate(self, ctx: EvaluatorContext[...]) -> float`, per
  https://pydantic.dev/docs/ai/evals/) — in particular whether returning a
  richer `EvaluationReason`/dict is preferable to a bare `float` for
  judge-style evaluators.
- The `ai:building-pydantic-ai-agents`, `pydantic-ai-harness:pydantic-ai-harness`,
  and `logfire:logfire-instrumentation` skills should be consulted again
  during implementation for exact call signatures, rather than relying solely
  on this design doc.

## Addendum: Corrections from the Final Whole-Branch Review

`LOGFIRE_PROJECT` was a mistaken assumption in this spec — Logfire has no such
concept; a project is derived from the token. `LogfireSettings` now has only a
`token` field, and it is wired into `configure_logfire()` (an addition to that
function's signature beyond what this spec originally described). See the
implementation plan's own addendum for the full list of corrections found
after all tasks landed.
