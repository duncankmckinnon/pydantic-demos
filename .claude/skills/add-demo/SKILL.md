---
name: add-demo
description: Use when adding a new demo application to the pydantic-demos repo, scaffolding a new apps/<name> service that shares demo_core, or wiring a new demo into the uv workspace and docker-compose.
---

# Add a Demo

## Overview

`pydantic-demos` hosts multiple local-only agentic demos, each a FastAPI app under
`apps/<name>/` sharing the `packages/demo_core` library (Gateway model helper, Logfire
setup, FastAPI app factory, eval-judge template). Every demo gets its own `.env`
(Gateway + Logfire credentials), its own UI, and its own Compose service/profile. See
`AGENTS.md` at the repo root for the full contract `demo_core` exposes.

## When to Use

Use when scaffolding a new `apps/<name>` demo, adding it to the uv workspace, or wiring it
into `docker-compose.yml`.

## Checklist

1. `apps/<name>/pyproject.toml` — copy `apps/chat/pyproject.toml`'s shape: depend on
   `demo-core` via `[tool.uv.sources] demo-core = { workspace = true }`, plus whatever the
   demo needs (`pydantic-ai`, `fastapi`, `uvicorn[standard]`, `pydantic-ai-harness` if the
   demo needs sandboxed tool orchestration or sub-agents).
2. `apps/<name>/.env.example` — at minimum `PYDANTIC_AI_GATEWAY_API_KEY`, `LOGFIRE_TOKEN`,
   `LOGFIRE_PROJECT`.
3. `apps/<name>/src/<name>/` — the agent(s) and FastAPI app. Build the agent with
   **REQUIRED SUB-SKILL: ai:building-pydantic-ai-agents**. If the demo needs sandboxed code
   execution, a filesystem/shell, sub-agents, or planning, add
   **REQUIRED SUB-SKILL: pydantic-ai-harness:pydantic-ai-harness** capabilities to that
   agent — `demo_core` itself has no harness dependency, so this is entirely the demo's
   choice. Call `demo_core.logfire_setup.configure_logfire(service_name, ...)` before
   constructing any Agent or building the FastAPI app; use
   **REQUIRED SUB-SKILL: logfire:logfire-instrumentation** for anything beyond that one call
   (extra spans, custom metrics, instrumenting another library).
4. `apps/<name>/src/<name>/evals/` — a `pydantic_evals.Dataset` of `Case`s. Use
   `demo_core.evals.HarnessJudge(agent=..., rubric=...)` for LLM-judge-style scoring.
5. `apps/<name>/tests/` — unit tests using `TestModel`/`FunctionModel` via
   `agent.override(model=...)`; a `conftest.py` setting dummy env vars via
   `os.environ.setdefault(...)` so settings objects can construct at import time without a
   real `.env`.
6. `apps/<name>/Dockerfile` — copy `apps/chat/Dockerfile`, swapping `chat` for `<name>`.
   Remember the build context is the **repo root**, not `apps/<name>`, because of the
   `demo-core` path dependency.
7. `docker-compose.yml` — add a service block for `<name>` (see `chat`'s entry), with its
   own `profiles: ["<name>", "all"]`. Chaining to another demo is not a special mechanism —
   just call that demo's Compose service name as an HTTP base URL.
8. Run `uv sync --all-packages` at the repo root to pick up the new workspace member (the
   root is a virtual workspace, so plain `uv sync` skips members nothing depends on), then
   `uv run pytest apps/<name>/tests/` and `docker compose config` before committing.

## Common Mistakes

- Forgetting `.env.example` (or committing a real `.env` — it must stay gitignored).
- Pointing the Dockerfile's build context at `apps/<name>` instead of the repo root.
- Adding agent-construction or harness-capability abstractions to `demo_core` before a
  second demo actually needs the same thing — copy the pattern from `chat` first.
