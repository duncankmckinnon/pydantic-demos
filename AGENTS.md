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
- `demo_core.settings.LogfireSettings(token: str)` — reads `LOGFIRE_TOKEN`.
- `demo_core.logfire_setup.configure_logfire(service_name, environment="local", send_to_logfire=True, token=None)`
  — call once, before building any Agent or FastAPI app. `token=None` lets Logfire read
  `LOGFIRE_TOKEN` from the ambient environment itself.
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
```

This gives each demo an independent Gateway project/token and an independent Logfire
project/token — the Logfire project is derived from the token itself, so there is no
separate project setting. `docker-compose.yml` loads each service's `.env` via its own
`env_file:`; when running outside Docker, the app loads its own `apps/<name>/.env` via
`load_dotenv()` at import time.

## Docker & chaining

`docker compose --profile <name> up` runs one demo. `--profile all` runs everything.
"Chaining" two demos together is not a special mechanism — a chained demo is just another
`apps/<name>` whose config points at another demo's Compose service name as a base URL and
calls it over HTTP, the same as any external API.

## Infrastructure monitoring

Each demo that wants container metrics gets its own OpenTelemetry Collector: `infra/otel-collector/<name>.yaml`
runs one via the `otel-collector-<name>` service in `docker-compose.yml`, using the
`docker_stats` receiver to send container CPU/memory/IO metrics to Logfire. It reuses that
demo's own `apps/<name>/.env` for `LOGFIRE_TOKEN` rather than minting a separate one — since
the Logfire project is derived from the token, this ties the collector's metrics to that
demo's project. It shares that demo's Compose profiles, so `docker compose --profile <name> up`
(or `--profile all`) starts it automatically; view the data under that project's
Docker/Infrastructure view in Logfire.

`docker_stats` reads from the shared `docker.sock` and sees *every* container on the host, not
just the one demo's — so each collector config includes a `filter` processor (e.g. `filter/chat`
in `infra/otel-collector/chat.yaml`) that keeps only metrics whose `compose.service` resource
attribute matches that demo's own Compose service name, dropping everything else (other demos'
containers, the collector's own container, unrelated host containers). Without it, every
collector would report the whole host's containers into its own project, duplicating data and
defeating the point of each demo owning its own credentials.

One collector should never hold more than one demo's `LOGFIRE_TOKEN` — routing different
containers to different projects from a single shared collector would mean centralizing every
demo's credentials into one place, which breaks the same per-app credential isolation
`apps/<name>/.env` exists to provide. Adding a new demo's infra monitoring means copying the
`otel-collector-chat` / `infra/otel-collector/chat.yaml` pattern with that demo's own name and
`.env`, not extending an existing collector.

## Testing & evals

- Unit tests (`apps/<name>/tests/`): use `pydantic_ai.models.test.TestModel` or
  `pydantic_ai.models.function.FunctionModel` via `agent.override(model=...)` — never call
  real models in the default test suite. Run with `uv run pytest`.
- Offline evals (`apps/<name>/src/<name>/evals/`): real-model-call `pydantic_evals` `Dataset`s,
  scored with built-in evaluators and/or `demo_core.evals.HarnessJudge`. Run manually via
  `uv run --package <name> python -m <name>.evals.run` — not part of the default test suite.
- Online evals: attach a `pydantic_evals.online_capability.OnlineEvaluation` capability to the
  agent (see `chat.evals.online.CHAT_ONLINE_EVALUATION`, wired in via `build_agent`'s
  `capabilities` param) to score real production/staging calls in the background — reuse the
  same `Evaluator` instances as the offline `Dataset` where it makes sense (e.g.
  `chat_quality_judge`) rather than defining the rubric twice. Sample real-model-call
  evaluators well below 1.0 (`OnlineEvaluator(..., sample_rate=0.2)`) since they run inline
  with production traffic; free/structural evaluators can run on every call. Results appear as
  `gen_ai.evaluation.result` OTel events in Logfire's Live Evaluations view. Every test suite
  must call `pydantic_evals.online.configure(enabled=False)` in `conftest.py` (see `chat`'s) —
  otherwise a sampled real-model-call evaluator fires in the background during ordinary
  endpoint tests.

## Adding a new demo

Use the `add-demo` project skill (`.claude/skills/add-demo/SKILL.md`) — it has the concrete
file-by-file checklist and points to the Pydantic AI / Harness / Logfire skills to use while
building the demo's actual agent logic.
