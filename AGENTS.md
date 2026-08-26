# AGENTS.md

## What this repo is

`pydantic-demos` hosts multiple local-only agentic demo applications, each built with
Pydantic AI (optionally Pydantic AI Harness), instrumented with Logfire, and evaluated
with Pydantic Evals. Demos target different customer types, so each has its own UI and
its own Pydantic AI Gateway / Logfire credentials. There is no production deployment,
secrets manager, or customer-facing auth in this repo — everything runs locally via Docker.

## Layout

- `packages/demo_core/` — shared library every demo depends on as an editable workspace
  package (`pyproject.toml` uses `[tool.uv.sources] demo-core = { workspace = true }`).
- `apps/<name>/` — one FastAPI demo per folder. Each has its own `pyproject.toml`, `.env`
  (gitignored) / `.env.example`, `Dockerfile`, `src/<name>/`, and `tests/`.
- `docker-compose.yml` — one service per demo, grouped into Compose `profiles` for running
  standalone or chained together.

## `demo_core` contract

- `demo_core.settings.GatewaySettings(api_key: str)` — reads `PYDANTIC_AI_GATEWAY_API_KEY`.
- `demo_core.settings.LogfireSettings(token: str, project: str | None)` — reads
  `LOGFIRE_TOKEN` / `LOGFIRE_PROJECT`.
- `demo_core.logfire_setup.configure_logfire(service_name, environment="local", send_to_logfire=True)`
  — call once, before building any Agent or FastAPI app.
- `demo_core.models.get_model(api_format, model_name, settings) -> Model` — the only place
  Gateway provider wiring lives. Add a new `api_format` by adding an entry to its
  `_MODEL_CLASSES` dict.
- `demo_core.web.create_app(title: str) -> FastAPI` — health check at `/health` plus a
  standard JSON error handler already registered.
- `demo_core.evals.HarnessJudge(agent, rubric)` — a `pydantic_evals.evaluators.Evaluator`
  that delegates scoring to an Agent; equip that Agent with `pydantic-ai-harness`
  capabilities (Shell, CodeMode, SubAgents) when the judgment needs more than one model call.

Nothing else lives in `demo_core` on purpose: no shared UI template (demos may need
different levels of frontend complexity), no customer-facing auth (nothing needs it yet),
and no shared agent-factory/harness-capability defaults (no second demo to prove the
abstraction yet — copy the pattern from `chat`, and only promote it into `demo_core` once
two demos need the same thing).

## Per-app credentials

Every `apps/<name>/.env` (gitignored) sets its own:

```
PYDANTIC_AI_GATEWAY_API_KEY=
LOGFIRE_TOKEN=
LOGFIRE_PROJECT=
```

This gives each demo an independent Gateway project/token and an independent Logfire
project/token. `docker-compose.yml` loads each service's `.env` via its own `env_file:`.

## Docker & chaining

`docker compose --profile <name> up` runs one demo. `--profile all` runs everything.
"Chaining" two demos together is not a special mechanism — a chained demo is just another
`apps/<name>` whose config points at another demo's Compose service name as a base URL and
calls it over HTTP, the same as any external API.

## Testing & evals

- Unit tests (`apps/<name>/tests/`): use `pydantic_ai.models.test.TestModel` or
  `pydantic_ai.models.function.FunctionModel` via `agent.override(model=...)` — never call
  real models in the default test suite. Run with `uv run pytest`.
- Evals (`apps/<name>/src/<name>/evals/`): real-model-call `pydantic_evals` `Dataset`s,
  scored with built-in evaluators and/or `demo_core.evals.HarnessJudge`. Run manually via
  `uv run --package <name> python -m <name>.evals.run` — not part of the default test suite.

## Adding a new demo

Use the `add-demo` project skill (`.claude/skills/add-demo/SKILL.md`) — it has the concrete
file-by-file checklist and points to the Pydantic AI / Harness / Logfire skills to use while
building the demo's actual agent logic.
