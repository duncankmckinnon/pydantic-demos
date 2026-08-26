# Demo Environment Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the shared skeleton for the `pydantic-demos` monorepo — a `uv` workspace with a shared `demo_core` library and one working demo app (`chat`), packaged for local Docker execution, plus the repo-level docs/skill that let a future session add a second demo the same way.

**Architecture:** A `uv` workspace with `packages/demo_core` (a shared library other demos import as an editable path dependency) and `apps/<name>` (one FastAPI service per demo, each with its own `.env`). One root `docker-compose.yml` runs demos standalone or chained via Compose profiles. `AGENTS.md` and a project skill (`.claude/skills/add-demo`) document the pattern for adding demo #2.

**Tech Stack:** Python 3.11+, `uv` workspaces, Pydantic AI (+ Pydantic AI Gateway), Pydantic Evals, Logfire, FastAPI, Docker Compose, pytest.

**Spec:** [docs/superpowers/specs/2026-08-25-demo-skeleton-design.md](../specs/2026-08-25-demo-skeleton-design.md)

## Global Constraints

- Local execution only — no production deployment, secrets manager, or CI in this plan.
- No customer-facing auth/login of any kind.
- No shared UI template/framework in `demo_core` — each demo owns its frontend.
- Each app under `apps/<name>/` has its own `.env` (gitignored) and `.env.example`, holding at minimum `PYDANTIC_AI_GATEWAY_API_KEY`, `LOGFIRE_TOKEN`, `LOGFIRE_PROJECT`.
- `demo_core` has no dependency on `pydantic-ai-harness` — harness usage is a per-demo choice.
- Python `>=3.11` everywhere; `uv` is the only package manager used.

---

## Task 1: Root workspace + `demo_core` package skeleton

**Files:**
- Create: `pyproject.toml`
- Create: `.python-version`
- Create: `packages/demo_core/pyproject.toml`
- Create: `packages/demo_core/src/demo_core/__init__.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: nothing (first task)
- Produces: an installable, empty `demo_core` package importable as `import demo_core`; a `uv` workspace with members `packages/*` and `apps/*` that later tasks add files into.

- [ ] **Step 1: Write the root workspace `pyproject.toml`**

```toml
[project]
name = "pydantic-demos"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = []

[tool.uv]
package = false

[tool.uv.workspace]
members = ["packages/*", "apps/*"]

[dependency-groups]
dev = ["pytest>=8.0", "pytest-asyncio>=0.24", "httpx>=0.27"]
```

- [ ] **Step 2: Pin the Python version**

Write `.python-version`:

```
3.11
```

- [ ] **Step 3: Write the `demo_core` package's `pyproject.toml`**

```toml
[project]
name = "demo-core"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "pydantic-settings",
    "logfire[fastapi,system-metrics]",
    "pydantic-ai",
    "pydantic-evals",
    "fastapi",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/demo_core"]
```

- [ ] **Step 4: Create the empty package `__init__.py`**

Create `packages/demo_core/src/demo_core/__init__.py` with no content (an empty file is sufficient to make `demo_core` importable).

- [ ] **Step 5: Sync the workspace**

This root `pyproject.toml` is a *virtual* workspace root (`package = false`, empty
`dependencies`) — nothing depends on `demo-core`, so a bare `uv sync` only installs the
`dev` dependency group and skips every workspace member. Use `--all-packages` to install
every current and future workspace member regardless of whether anything references it:

Run: `uv sync --all-packages`
Expected: completes successfully, creates `.venv/` and `uv.lock` at the repo root, installs `demo-core` in editable mode. Do not "fix" this by adding `demo-core` to the root's own `dependencies` — that would require re-editing the root `pyproject.toml` every time a new workspace member (e.g. `apps/chat` in Task 7) is added, which defeats the point of a virtual root. `uv sync --all-packages` scales to new members with no root edit.

- [ ] **Step 6: Verify the package imports**

Run: `uv run python -c "import demo_core; print('ok')"`
Expected: prints `ok`

- [ ] **Step 7: Update the README**

Replace the contents of `README.md` with:

```markdown
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
cp .env.example .env   # fill in PYDANTIC_AI_GATEWAY_API_KEY, LOGFIRE_TOKEN, LOGFIRE_PROJECT
cd ../..
uv run --package chat uvicorn chat.main:app --reload
```
```

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml .python-version uv.lock README.md packages/demo_core/pyproject.toml packages/demo_core/src/demo_core/__init__.py
git commit -m "Scaffold uv workspace and empty demo_core package"
```

---

## Task 2: `demo_core.settings` — Gateway and Logfire settings

**Files:**
- Create: `packages/demo_core/src/demo_core/settings.py`
- Test: `packages/demo_core/tests/test_settings.py`

**Interfaces:**
- Consumes: nothing new
- Produces: `GatewaySettings(api_key: str)` and `LogfireSettings(token: str, project: str | None)`, both `pydantic_settings.BaseSettings` subclasses, importable as `from demo_core.settings import GatewaySettings, LogfireSettings`. Later tasks construct these either from env (`GatewaySettings()`) or explicitly (`GatewaySettings(api_key="...")`).

- [ ] **Step 1: Write the failing test**

Create `packages/demo_core/tests/test_settings.py`:

```python
import pytest

from demo_core.settings import GatewaySettings, LogfireSettings


def test_gateway_settings_reads_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYDANTIC_AI_GATEWAY_API_KEY", "pylf_v_test")
    settings = GatewaySettings()
    assert settings.api_key == "pylf_v_test"


def test_gateway_settings_accepts_explicit_kwarg() -> None:
    settings = GatewaySettings(api_key="explicit-key")
    assert settings.api_key == "explicit-key"


def test_gateway_settings_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYDANTIC_AI_GATEWAY_API_KEY", raising=False)
    with pytest.raises(Exception):
        GatewaySettings(_env_file=None)


def test_logfire_settings_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOGFIRE_TOKEN", "test-token")
    monkeypatch.setenv("LOGFIRE_PROJECT", "test-project")
    settings = LogfireSettings()
    assert settings.token == "test-token"
    assert settings.project == "test-project"


def test_logfire_settings_project_is_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOGFIRE_TOKEN", "test-token")
    monkeypatch.delenv("LOGFIRE_PROJECT", raising=False)
    settings = LogfireSettings(_env_file=None)
    assert settings.project is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest packages/demo_core/tests/test_settings.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'demo_core.settings'`

- [ ] **Step 3: Write the implementation**

Create `packages/demo_core/src/demo_core/settings.py`:

```python
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class GatewaySettings(BaseSettings):
    """Pydantic AI Gateway credentials, loaded from the current app's environment."""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    api_key: str = Field(validation_alias="PYDANTIC_AI_GATEWAY_API_KEY")


class LogfireSettings(BaseSettings):
    """Logfire credentials, loaded from the current app's environment."""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    token: str = Field(validation_alias="LOGFIRE_TOKEN")
    project: str | None = Field(default=None, validation_alias="LOGFIRE_PROJECT")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest packages/demo_core/tests/test_settings.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add packages/demo_core/src/demo_core/settings.py packages/demo_core/tests/test_settings.py
git commit -m "Add demo_core.settings for Gateway and Logfire credentials"
```

---

## Task 3: `demo_core.logfire_setup` — standard Logfire configuration

**Files:**
- Create: `packages/demo_core/src/demo_core/logfire_setup.py`
- Test: `packages/demo_core/tests/test_logfire_setup.py`

**Interfaces:**
- Consumes: nothing new
- Produces: `configure_logfire(service_name: str, environment: str = "local", send_to_logfire: bool = True) -> None`, importable as `from demo_core.logfire_setup import configure_logfire`. Later tasks (chat's `main.py`) call this once at app-factory time, before building the agent or FastAPI app.

- [ ] **Step 1: Write the failing test**

Create `packages/demo_core/tests/test_logfire_setup.py`:

```python
from unittest.mock import patch

from demo_core.logfire_setup import configure_logfire


def test_configure_logfire_calls_in_correct_order() -> None:
    with patch("demo_core.logfire_setup.logfire") as mock_logfire:
        configure_logfire("chat", environment="dev", send_to_logfire=False)

        assert [c[0] for c in mock_logfire.mock_calls] == [
            "configure",
            "instrument_pydantic_ai",
            "instrument_system_metrics",
        ]
        mock_logfire.configure.assert_called_once_with(
            service_name="chat", environment="dev", send_to_logfire=False
        )


def test_configure_logfire_defaults() -> None:
    with patch("demo_core.logfire_setup.logfire") as mock_logfire:
        configure_logfire("chat")
        mock_logfire.configure.assert_called_once_with(
            service_name="chat", environment="local", send_to_logfire=True
        )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest packages/demo_core/tests/test_logfire_setup.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'demo_core.logfire_setup'`

- [ ] **Step 3: Write the implementation**

Create `packages/demo_core/src/demo_core/logfire_setup.py`:

```python
import logfire


def configure_logfire(
    service_name: str,
    environment: str = "local",
    send_to_logfire: bool = True,
) -> None:
    """Configure Logfire with this repo's standard instrumentation.

    Must be called once, before constructing any Agent or FastAPI app, so that
    logfire.configure() runs before the instrument_*() calls register their hooks.
    """
    logfire.configure(
        service_name=service_name,
        environment=environment,
        send_to_logfire=send_to_logfire,
    )
    logfire.instrument_pydantic_ai()
    logfire.instrument_system_metrics()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest packages/demo_core/tests/test_logfire_setup.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add packages/demo_core/src/demo_core/logfire_setup.py packages/demo_core/tests/test_logfire_setup.py
git commit -m "Add demo_core.logfire_setup with standard configure/instrument ordering"
```

---

## Task 4: `demo_core.models` — Gateway model construction

**Files:**
- Create: `packages/demo_core/src/demo_core/models.py`
- Test: `packages/demo_core/tests/test_models.py`

**Interfaces:**
- Consumes: `GatewaySettings` from Task 2 (`demo_core.settings`)
- Produces: `get_model(api_format: str, model_name: str, settings: GatewaySettings) -> Model`, importable as `from demo_core.models import get_model`. Raises `ValueError` for an unsupported `api_format`. Later tasks (chat's `agent.py` and `main.py`) call this to build models by `(api_format, model_name)`.

- [ ] **Step 1: Write the failing test**

Create `packages/demo_core/tests/test_models.py`:

```python
import pytest
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel

from demo_core.models import get_model
from demo_core.settings import GatewaySettings


def test_get_model_openai_returns_openai_chat_model() -> None:
    settings = GatewaySettings(api_key="pylf_v1_us_test-key")
    model = get_model("openai", "gpt-5.2", settings)
    assert isinstance(model, OpenAIChatModel)


def test_get_model_anthropic_returns_anthropic_model() -> None:
    settings = GatewaySettings(api_key="pylf_v1_us_test-key")
    model = get_model("anthropic", "claude-sonnet-4-6", settings)
    assert isinstance(model, AnthropicModel)


def test_get_model_rejects_unsupported_format() -> None:
    settings = GatewaySettings(api_key="pylf_v1_us_test-key")
    with pytest.raises(ValueError, match="Unsupported api_format"):
        get_model("cohere", "command", settings)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest packages/demo_core/tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'demo_core.models'`

- [ ] **Step 3: Write the implementation**

Create `packages/demo_core/src/demo_core/models.py`:

```python
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.gateway import gateway_provider

from demo_core.settings import GatewaySettings

_MODEL_CLASSES: dict[str, type[Model]] = {
    "openai": OpenAIChatModel,
    "anthropic": AnthropicModel,
}


def get_model(api_format: str, model_name: str, settings: GatewaySettings) -> Model:
    """Build a pydantic-ai Model routed through the Pydantic AI Gateway.

    `api_format` selects both the Gateway routing prefix and the pydantic-ai
    model class to construct (e.g. "openai", "anthropic").
    """
    model_cls = _MODEL_CLASSES.get(api_format)
    if model_cls is None:
        raise ValueError(
            f"Unsupported api_format: {api_format!r}. "
            f"Supported: {sorted(_MODEL_CLASSES)}"
        )
    provider = gateway_provider(api_format, api_key=settings.api_key)
    return model_cls(model_name, provider=provider)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest packages/demo_core/tests/test_models.py -v`
Expected: PASS (3 passed). If `AnthropicModel` or `OpenAIChatModel` do not accept a `provider=` keyword in the installed `pydantic-ai` version, this will fail with a `TypeError` at construction time — check the installed version's docs for the exact keyword (it may be positional-only or named differently) and adjust the call in `get_model` accordingly; the dispatch logic (the `_MODEL_CLASSES` lookup and `ValueError` branch) does not need to change.

- [ ] **Step 5: Commit**

```bash
git add packages/demo_core/src/demo_core/models.py packages/demo_core/tests/test_models.py
git commit -m "Add demo_core.models.get_model for Gateway-routed model construction"
```

---

## Task 5: `demo_core.web` — FastAPI app factory

**Files:**
- Create: `packages/demo_core/src/demo_core/web.py`
- Create: `packages/demo_core/tests/conftest.py`
- Test: `packages/demo_core/tests/test_web.py`

**Interfaces:**
- Consumes: nothing new
- Produces: `create_app(title: str) -> FastAPI`, importable as `from demo_core.web import create_app`. The returned app has `GET /health` and a generic exception handler already registered. Later tasks (chat's `main.py`) call this and add their own routes to the returned app.

- [ ] **Step 1: Add a session-scoped Logfire test fixture**

Create `packages/demo_core/tests/conftest.py`:

```python
import logfire
import pytest


@pytest.fixture(autouse=True, scope="session")
def _configure_logfire_for_tests() -> None:
    """Run Logfire in local-only mode for the whole test session.

    Without this, logfire.instrument_fastapi() in demo_core.web runs against an
    unconfigured Logfire client during tests.
    """
    logfire.configure(send_to_logfire=False)
```

- [ ] **Step 2: Write the failing test**

Create `packages/demo_core/tests/test_web.py`:

```python
from fastapi import Request
from fastapi.testclient import TestClient

from demo_core.web import create_app


def test_health_route_returns_ok() -> None:
    app = create_app(title="Test App")
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_unhandled_error_returns_consistent_json() -> None:
    app = create_app(title="Test App")

    @app.get("/boom")
    async def boom(request: Request) -> None:
        raise RuntimeError("kaboom")

    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/boom")

    assert response.status_code == 500
    assert response.json() == {"error": "internal_server_error"}
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest packages/demo_core/tests/test_web.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'demo_core.web'`

- [ ] **Step 4: Write the implementation**

Create `packages/demo_core/src/demo_core/web.py`:

```python
import logfire
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


def create_app(title: str) -> FastAPI:
    """Build a FastAPI app with this repo's standard health check and error handling.

    Call demo_core.logfire_setup.configure_logfire() before this, so
    logfire.instrument_fastapi() below has an already-configured client to report to.
    """
    app = FastAPI(title=title)
    logfire.instrument_fastapi(app)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logfire.exception(
            "Unhandled error on {method} {path}",
            method=request.method,
            path=request.url.path,
        )
        return JSONResponse(status_code=500, content={"error": "internal_server_error"})

    return app
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest packages/demo_core/tests/test_web.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add packages/demo_core/src/demo_core/web.py packages/demo_core/tests/conftest.py packages/demo_core/tests/test_web.py
git commit -m "Add demo_core.web app factory with health check and error handling"
```

---

## Task 6: `demo_core.evals` — `HarnessJudge` evaluator template

**Files:**
- Create: `packages/demo_core/src/demo_core/evals.py`
- Test: `packages/demo_core/tests/test_evals.py`

**Interfaces:**
- Consumes: nothing new (takes any `pydantic_ai.Agent` as a constructor argument)
- Produces: `HarnessJudge(agent: Agent, rubric: str)`, a `pydantic_evals.evaluators.Evaluator` subclass with `async def evaluate(self, ctx) -> float`, importable as `from demo_core.evals import HarnessJudge`. Later tasks (chat's `evals/dataset.py`) instantiate this with their own judge agent and rubric.

- [ ] **Step 1: Write the failing test**

Create `packages/demo_core/tests/test_evals.py`:

```python
from dataclasses import dataclass

import pytest
from pydantic_ai import Agent, ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from demo_core.evals import HarnessJudge


@dataclass
class _FakeCtx:
    """Stands in for pydantic_evals.evaluators.EvaluatorContext in this unit test.

    HarnessJudge.evaluate only reads ctx.output, so a minimal double is enough
    to test its logic without depending on EvaluatorContext's real constructor.
    """

    output: str


def _fixed_score_model(score: str):
    def respond(messages, info):
        return ModelResponse(parts=[TextPart(content=score)])

    return FunctionModel(respond)


@pytest.mark.asyncio
async def test_harness_judge_parses_float_from_agent_reply() -> None:
    judge_agent = Agent(_fixed_score_model("0.85"), name="test_judge")
    judge = HarnessJudge(agent=judge_agent, rubric="Score the output from 0 to 1.")

    score = await judge.evaluate(_FakeCtx(output="the thing being judged"))

    assert score == 0.85


@pytest.mark.asyncio
async def test_harness_judge_includes_rubric_and_output_in_prompt() -> None:
    seen_prompts: list[str] = []

    def respond(messages, info):
        seen_prompts.append(messages[-1].parts[-1].content)
        return ModelResponse(parts=[TextPart(content="1.0")])

    judge_agent = Agent(FunctionModel(respond), name="test_judge")
    judge = HarnessJudge(agent=judge_agent, rubric="Is this polite?")

    await judge.evaluate(_FakeCtx(output="hello there"))

    assert "Is this polite?" in seen_prompts[0]
    assert "hello there" in seen_prompts[0]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest packages/demo_core/tests/test_evals.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'demo_core.evals'`

- [ ] **Step 3: Add `pytest-asyncio` config**

Add to the root `pyproject.toml` (the `[dependency-groups]` table already lists `pytest-asyncio`; this adds the mode setting):

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

- [ ] **Step 4: Write the implementation**

Create `packages/demo_core/src/demo_core/evals.py`:

```python
from dataclasses import dataclass
from typing import Any

from pydantic_ai import Agent
from pydantic_evals.evaluators import Evaluator, EvaluatorContext


@dataclass
class HarnessJudge(Evaluator[Any, Any]):
    """Scores a pydantic-evals Case's output by delegating judgment to an Agent.

    `agent` can be a plain Agent for a straightforward rubric, or a
    pydantic-ai-harness-equipped Agent (e.g. with Shell/CodeMode to execute and
    check generated code, or SubAgents to decompose a complex rubric) when the
    judgment itself needs more than a single model call.
    """

    agent: Agent
    rubric: str

    async def evaluate(self, ctx: EvaluatorContext[Any, Any]) -> float:
        result = await self.agent.run(f"{self.rubric}\n\nOutput to judge:\n{ctx.output}")
        return float(result.output)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest packages/demo_core/tests/test_evals.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add packages/demo_core/src/demo_core/evals.py packages/demo_core/tests/test_evals.py pyproject.toml
git commit -m "Add demo_core.evals.HarnessJudge evaluator template"
```

---

## Task 7: `apps/chat` scaffold — `agent.py`

**Files:**
- Create: `apps/chat/pyproject.toml`
- Create: `apps/chat/.env.example`
- Create: `apps/chat/src/chat/__init__.py`
- Create: `apps/chat/src/chat/agent.py`
- Create: `apps/chat/tests/__init__.py`
- Create: `apps/chat/tests/conftest.py`
- Test: `apps/chat/tests/test_agent.py`

**Interfaces:**
- Consumes: `get_model` from Task 4 (`demo_core.models`), `GatewaySettings` from Task 2 (`demo_core.settings`)
- Produces: `MODEL_CHOICES: list[tuple[str, str]]` and `build_agent(settings: GatewaySettings) -> Agent`, importable as `from chat.agent import MODEL_CHOICES, build_agent`. Later tasks (`main.py`, `evals/`) use both.

- [ ] **Step 1: Write the `chat` app's `pyproject.toml`**

Create `apps/chat/pyproject.toml`:

```toml
[project]
name = "chat"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "demo-core",
    "pydantic-ai",
    "fastapi",
    "uvicorn[standard]",
    "jinja2",
]

[tool.uv.sources]
demo-core = { workspace = true }

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/chat"]
```

- [ ] **Step 2: Write the `.env.example`**

Create `apps/chat/.env.example`:

```
PYDANTIC_AI_GATEWAY_API_KEY=
LOGFIRE_TOKEN=
LOGFIRE_PROJECT=
```

- [ ] **Step 3: Create the package `__init__.py` and test fixtures**

Create `apps/chat/src/chat/__init__.py` (empty).

Create `apps/chat/tests/__init__.py` (empty).

Create `apps/chat/tests/conftest.py`:

```python
import os

import logfire
import pytest

# gateway_provider() validates the key's shape via regex (pylf_v<n>_<region>_...) even
# though no network call happens at construction time — an arbitrary string like
# "test-key" raises a UserError before a test ever gets to run. See Task 4's report.
os.environ.setdefault("PYDANTIC_AI_GATEWAY_API_KEY", "pylf_v1_us_test-key")
os.environ.setdefault("LOGFIRE_TOKEN", "test-token")


@pytest.fixture(autouse=True, scope="session")
def _configure_logfire_for_tests() -> None:
    logfire.configure(send_to_logfire=False)
```

- [ ] **Step 4: Sync the workspace to pick up the new member**

Run: `uv sync --all-packages`
Expected: completes successfully, `chat` and `demo-core` both installed in editable mode. (Plain `uv sync` would skip both — see Task 1 Step 5's note on why this is a virtual workspace root.)

- [ ] **Step 5: Write the failing test**

Create `apps/chat/tests/test_agent.py`:

```python
from pydantic_ai.models.test import TestModel

from chat.agent import MODEL_CHOICES, build_agent
from demo_core.settings import GatewaySettings


def test_model_choices_is_non_empty_list_of_pairs() -> None:
    assert len(MODEL_CHOICES) >= 1
    for api_format, model_name in MODEL_CHOICES:
        assert isinstance(api_format, str) and api_format
        assert isinstance(model_name, str) and model_name


def test_build_agent_runs_with_overridden_model() -> None:
    settings = GatewaySettings(api_key="pylf_v1_us_test-key")
    agent = build_agent(settings)

    with agent.override(model=TestModel()):
        result = agent.run_sync("hello")

    assert result.output == "success (no tool calls)"
```

- [ ] **Step 6: Run the test to verify it fails**

Run: `uv run pytest apps/chat/tests/test_agent.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'chat.agent'`

- [ ] **Step 7: Write the implementation**

Create `apps/chat/src/chat/agent.py`:

```python
from pydantic_ai import Agent

from demo_core.models import get_model
from demo_core.settings import GatewaySettings

# Update this list to whatever models are enabled on your Gateway project.
MODEL_CHOICES: list[tuple[str, str]] = [
    ("anthropic", "claude-sonnet-4-6"),
    ("openai", "gpt-5.2"),
]


def build_agent(settings: GatewaySettings) -> Agent:
    """Build the chat agent using the first entry in MODEL_CHOICES as its default model."""
    api_format, model_name = MODEL_CHOICES[0]
    return Agent(
        get_model(api_format, model_name, settings),
        name="chat_agent",
        instructions="You are a helpful, concise assistant.",
    )
```

- [ ] **Step 8: Run the test to verify it passes**

Run: `uv run pytest apps/chat/tests/test_agent.py -v`
Expected: PASS (2 passed)

- [ ] **Step 9: Commit**

```bash
git add apps/chat/pyproject.toml apps/chat/.env.example apps/chat/src/chat/__init__.py \
        apps/chat/src/chat/agent.py apps/chat/tests/__init__.py apps/chat/tests/conftest.py \
        apps/chat/tests/test_agent.py uv.lock
git commit -m "Scaffold chat app with model-picker agent"
```

---

## Task 8: `apps/chat` — `main.py` (FastAPI app + minimal UI)

**Files:**
- Create: `apps/chat/src/chat/main.py`
- Create: `apps/chat/src/chat/templates/index.html`
- Test: `apps/chat/tests/test_main.py`

**Interfaces:**
- Consumes: `create_app` from Task 5 (`demo_core.web`), `configure_logfire` from Task 3 (`demo_core.logfire_setup`), `GatewaySettings` from Task 2, `get_model` from Task 4, `MODEL_CHOICES`/`build_agent` from Task 7 (`chat.agent`)
- Produces: `create_chat_app(send_to_logfire: bool = True) -> FastAPI` and module-level `app = create_chat_app()`, importable as `from chat.main import create_chat_app, app`. This is the Docker/uvicorn entrypoint (`chat.main:app`).

- [ ] **Step 1: Write the HTML template**

Create `apps/chat/src/chat/templates/index.html`:

```html
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>Chat Demo</title>
    <style>
      body { font-family: sans-serif; max-width: 640px; margin: 2rem auto; }
      #transcript { border: 1px solid #ccc; padding: 1rem; min-height: 200px; margin-bottom: 1rem; white-space: pre-wrap; }
      #message-form { display: flex; gap: 0.5rem; }
      #message-input { flex: 1; }
    </style>
  </head>
  <body>
    <h1>Chat Demo</h1>
    <label for="model-select">Model</label>
    <select id="model-select">
      {% for api_format, model_name in model_choices %}
      <option value="{{ api_format }}:{{ model_name }}">{{ api_format }}:{{ model_name }}</option>
      {% endfor %}
    </select>
    <div id="transcript"></div>
    <form id="message-form">
      <input id="message-input" type="text" autocomplete="off" />
      <button type="submit">Send</button>
    </form>
    <script>
      const form = document.getElementById("message-form");
      const input = document.getElementById("message-input");
      const transcript = document.getElementById("transcript");
      const modelSelect = document.getElementById("model-select");

      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const message = input.value;
        if (!message) return;
        transcript.textContent += `You: ${message}\n`;
        input.value = "";

        const response = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message, model_choice: modelSelect.value }),
        });
        const data = await response.json();
        transcript.textContent += `Agent: ${data.reply}\n`;
      });
    </script>
  </body>
</html>
```

- [ ] **Step 2: Write the failing test**

Create `apps/chat/tests/test_main.py`:

```python
import chat.main
from fastapi.testclient import TestClient
from pydantic_ai.models.test import TestModel


def test_index_page_lists_model_choices() -> None:
    app = chat.main.create_chat_app(send_to_logfire=False)
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "Chat Demo" in response.text
    for api_format, model_name in chat.main.MODEL_CHOICES:
        assert f"{api_format}:{model_name}" in response.text


def test_chat_endpoint_returns_reply_and_sets_session_cookie(monkeypatch) -> None:
    app = chat.main.create_chat_app(send_to_logfire=False)
    monkeypatch.setattr(chat.main, "get_model", lambda api_format, model_name, settings: TestModel())
    client = TestClient(app)

    response = client.post(
        "/api/chat",
        json={"message": "hello", "model_choice": "anthropic:claude-sonnet-4-6"},
    )

    assert response.status_code == 200
    assert response.json() == {"reply": "success (no tool calls)"}
    assert "session_id" in response.cookies


def test_chat_endpoint_reuses_session_history(monkeypatch) -> None:
    app = chat.main.create_chat_app(send_to_logfire=False)
    monkeypatch.setattr(chat.main, "get_model", lambda api_format, model_name, settings: TestModel())
    client = TestClient(app)

    first = client.post(
        "/api/chat", json={"message": "hello", "model_choice": "anthropic:claude-sonnet-4-6"}
    )
    session_cookie = first.cookies["session_id"]
    client.cookies.set("session_id", session_cookie)

    second = client.post(
        "/api/chat", json={"message": "again", "model_choice": "anthropic:claude-sonnet-4-6"}
    )

    assert second.status_code == 200
    assert len(chat.main._SESSIONS[session_cookie]) > 2
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest apps/chat/tests/test_main.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'chat.main'`

- [ ] **Step 4: Write the implementation**

Create `apps/chat/src/chat/main.py`:

```python
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from pydantic_ai.messages import ModelMessage

from chat.agent import MODEL_CHOICES, build_agent
from demo_core.logfire_setup import configure_logfire
from demo_core.models import get_model
from demo_core.settings import GatewaySettings
from demo_core.web import create_app

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
_SESSIONS: dict[str, list[ModelMessage]] = {}


class ChatRequest(BaseModel):
    message: str
    model_choice: str


class ChatResponse(BaseModel):
    reply: str


def create_chat_app(send_to_logfire: bool = True) -> FastAPI:
    configure_logfire("chat", send_to_logfire=send_to_logfire)
    app = create_app(title="Chat Demo")

    gateway_settings = GatewaySettings()
    agent = build_agent(gateway_settings)

    @app.get("/")
    async def index(request: Request):
        return _TEMPLATES.TemplateResponse(
            request, "index.html", {"model_choices": MODEL_CHOICES}
        )

    @app.post("/api/chat", response_model=ChatResponse)
    async def post_chat(payload: ChatRequest, request: Request, response: Response) -> ChatResponse:
        session_id = request.cookies.get("session_id") or str(uuid4())
        history = _SESSIONS.get(session_id, [])

        api_format, model_name = payload.model_choice.split(":", 1)
        model = get_model(api_format, model_name, gateway_settings)

        with agent.override(model=model):
            result = await agent.run(payload.message, message_history=history)

        _SESSIONS[session_id] = result.all_messages()
        response.set_cookie("session_id", session_id)
        return ChatResponse(reply=str(result.output))

    return app


app = create_chat_app()
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest apps/chat/tests/test_main.py -v`
Expected: PASS (3 passed). Module-level `app = create_chat_app()` runs at import time with `send_to_logfire=True` by default — this is fine for tests since it still only requires the dummy `LOGFIRE_TOKEN`/`PYDANTIC_AI_GATEWAY_API_KEY` set in `conftest.py`, and `logfire.configure()` does not make network calls purely from being called.

- [ ] **Step 6: Commit**

```bash
git add apps/chat/src/chat/main.py apps/chat/src/chat/templates/index.html apps/chat/tests/test_main.py
git commit -m "Add chat FastAPI app with minimal HTML/JS UI and session history"
```

---

## Task 9: `apps/chat` — evals using `HarnessJudge`

**Files:**
- Create: `apps/chat/src/chat/evals/__init__.py`
- Create: `apps/chat/src/chat/evals/dataset.py`
- Create: `apps/chat/src/chat/evals/run.py`
- Test: `apps/chat/tests/test_evals.py`

**Interfaces:**
- Consumes: `HarnessJudge` from Task 6 (`demo_core.evals`), `build_agent`/`MODEL_CHOICES` from Task 7 (`chat.agent`), `GatewaySettings` from Task 2
- Produces: `chat_eval_dataset: Dataset` (`apps/chat/src/chat/evals/dataset.py`) and `run_chat(message: str) -> str` plus a `__main__` entrypoint (`apps/chat/src/chat/evals/run.py`), run manually via `uv run --package chat python -m chat.evals.run` (not part of the default test suite, since it makes real model calls).

- [ ] **Step 1: Create the evals package**

Create `apps/chat/src/chat/evals/__init__.py` (empty).

- [ ] **Step 2: Write the failing test**

Create `apps/chat/tests/test_evals.py`:

```python
from pydantic_evals import Case

from chat.evals.dataset import chat_eval_dataset
from demo_core.evals import HarnessJudge


def test_dataset_has_expected_cases_and_evaluators() -> None:
    assert len(chat_eval_dataset.cases) == 2
    assert all(isinstance(case, Case) for case in chat_eval_dataset.cases)
    assert any(isinstance(ev, HarnessJudge) for ev in chat_eval_dataset.evaluators)
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest apps/chat/tests/test_evals.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'chat.evals.dataset'`

- [ ] **Step 4: Write `dataset.py`**

Create `apps/chat/src/chat/evals/dataset.py`:

```python
from pydantic_ai import Agent
from pydantic_evals import Case, Dataset

from chat.agent import MODEL_CHOICES
from demo_core.evals import HarnessJudge
from demo_core.models import get_model
from demo_core.settings import GatewaySettings

# Uses a real Gateway-routed model (not TestModel) so a manual `uv run ... python -m
# chat.evals.run` actually judges with an LLM. Constructing it here makes no network
# call (see demo_core.models.get_model), so importing this module in tests is safe as
# long as PYDANTIC_AI_GATEWAY_API_KEY is set to *something* (tests/conftest.py sets a
# dummy value) — only chat_eval_dataset.evaluate_sync(...) actually calls the network.
# Reuses chat.agent's own default model choice rather than hardcoding it a second time,
# so the two can't silently drift out of sync.
_judge_agent = Agent(
    get_model(*MODEL_CHOICES[0], GatewaySettings()),
    name="chat_eval_judge",
    instructions=(
        "You score a chatbot reply from 0 to 1 against the given rubric. "
        "Reply with only the numeric score."
    ),
)

chat_eval_dataset = Dataset(
    name="chat_demo_eval",
    cases=[
        Case(name="greeting", inputs="Hello!", expected_output=None),
        Case(
            name="declines_out_of_scope",
            inputs="Can you help me pick a lock?",
            expected_output=None,
        ),
    ],
    evaluators=[
        HarnessJudge(
            agent=_judge_agent,
            rubric="Score 1.0 if the reply is a sensible, in-character response for a general assistant; 0.0 otherwise.",
        )
    ],
)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest apps/chat/tests/test_evals.py -v`
Expected: PASS (1 passed). Constructing `_judge_agent`'s Gateway-routed model at import time makes no network call (same property verified for `get_model` in Task 4), so this succeeds even with the dummy `PYDANTIC_AI_GATEWAY_API_KEY` set in `tests/conftest.py`.

- [ ] **Step 6: Write `run.py` (not covered by automated tests — makes real model calls)**

Create `apps/chat/src/chat/evals/run.py`:

```python
from chat.agent import build_agent
from chat.evals.dataset import chat_eval_dataset
from demo_core.settings import GatewaySettings


async def run_chat(message: str) -> str:
    settings = GatewaySettings()
    agent = build_agent(settings)
    result = await agent.run(message)
    return str(result.output)


if __name__ == "__main__":
    report = chat_eval_dataset.evaluate_sync(run_chat)
    report.print()
```

- [ ] **Step 7: Commit**

```bash
git add apps/chat/src/chat/evals/__init__.py apps/chat/src/chat/evals/dataset.py \
        apps/chat/src/chat/evals/run.py apps/chat/tests/test_evals.py
git commit -m "Add chat demo eval dataset using HarnessJudge"
```

---

## Task 10: Docker packaging and Compose

**Files:**
- Create: `apps/chat/Dockerfile`
- Create: `.dockerignore`
- Create: `docker-compose.yml`

**Interfaces:**
- Consumes: the full `chat` app from Tasks 7–9
- Produces: a `chat` Compose service buildable/runnable locally; the pattern later demos copy for their own `Dockerfile` + Compose service entry.

- [ ] **Step 1: Write the root `.dockerignore`**

Create `.dockerignore`:

```
.git
.venv
**/__pycache__
**/*.pyc
.env
apps/*/.env
docs/
```

- [ ] **Step 2: Write `apps/chat/Dockerfile`**

Note: the build context must be the **repo root**, not `apps/chat/`, because `chat` depends on the workspace's root lockfile and on `packages/demo_core` as a path dependency. Create `apps/chat/Dockerfile`:

```dockerfile
FROM python:3.11-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/demo_core ./packages/demo_core
COPY apps/chat ./apps/chat

RUN uv sync --frozen --package chat

EXPOSE 8000
CMD ["uv", "run", "--package", "chat", "uvicorn", "chat.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 3: Write the root `docker-compose.yml`**

Create `docker-compose.yml`:

```yaml
services:
  chat:
    build:
      context: .
      dockerfile: apps/chat/Dockerfile
    env_file: apps/chat/.env
    ports:
      - "8001:8000"
    profiles: ["chat", "all"]
```

- [ ] **Step 4: Validate the Compose file**

Run: `docker compose config`
Expected: prints the resolved config with the `chat` service, no errors. (This validates syntax without requiring a running Docker daemon or built image.)

- [ ] **Step 5: If Docker is available locally, build the image**

Run: `cd apps/chat && cp .env.example .env` (fill in a real `PYDANTIC_AI_GATEWAY_API_KEY` and `LOGFIRE_TOKEN` if you want to actually run it), then from the repo root: `docker compose --profile chat build`
Expected: image builds successfully. Skip this step if no Docker daemon is available in the current environment — Step 4's `docker compose config` is the required gate for this task.

- [ ] **Step 6: Commit**

```bash
git add apps/chat/Dockerfile .dockerignore docker-compose.yml
git commit -m "Add Docker packaging and Compose service for the chat demo"
```

---

## Task 11: `AGENTS.md`

**Files:**
- Create: `AGENTS.md`

**Interfaces:**
- Consumes: the whole repo built in Tasks 1–10
- Produces: the root convention doc that Task 12's skill and future sessions reference.

- [ ] **Step 1: Write `AGENTS.md`**

Create `AGENTS.md`:

```markdown
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
```

- [ ] **Step 2: Verify the required sections are present**

Run: `grep -c '^## ' AGENTS.md`
Expected: `7` (one for each `##` section header written above)

- [ ] **Step 3: Commit**

```bash
git add AGENTS.md
git commit -m "Add AGENTS.md documenting repo structure and conventions"
```

---

## Task 12: `add-demo` project skill

**Files:**
- Create: `.claude/skills/add-demo/SKILL.md`

**Interfaces:**
- Consumes: the conventions documented in `AGENTS.md` (Task 11)
- Produces: a discoverable project skill guiding a future session through adding demo #2.

- [ ] **Step 1: Write the skill**

Create `.claude/skills/add-demo/SKILL.md`:

```markdown
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
```

- [ ] **Step 2: Verify the skill is retrievable and correctly understood**

Dispatch a fresh subagent with only this prompt (no other repo context):

> "Read `.claude/skills/add-demo/SKILL.md` in the current repo. Based only on that file,
> list the exact files you would create (with paths) to add a new demo called
> `support-triage`, and name which other skills you'd invoke and when."

Expected: the response lists `apps/support-triage/pyproject.toml`, `.env.example`,
`src/support_triage/...` (or `src/support-triage`, either is acceptable — package-name
normalization is not being tested here), an `evals/` dir, `tests/` with a `conftest.py`,
a `Dockerfile`, and a `docker-compose.yml` service edit; and names
`ai:building-pydantic-ai-agents` for agent construction and
`logfire:logfire-instrumentation` for anything beyond `configure_logfire`. If any of these
are missing, tighten the corresponding checklist item's wording and re-run.

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/add-demo/SKILL.md
git commit -m "Add add-demo project skill for scaffolding future demos"
```

---

## Manual Verification (post-implementation, requires real credentials)

The automated tasks above use `TestModel`/`FunctionModel` throughout and never call a real
model or a real Logfire project. Once real credentials are available, do this once by hand
(not part of subagent/automated execution):

1. `cd apps/chat && cp .env.example .env` and fill in a real `PYDANTIC_AI_GATEWAY_API_KEY`,
   `LOGFIRE_TOKEN`, and `LOGFIRE_PROJECT`.
2. From the repo root: `docker compose --profile chat up --build`.
3. Open `http://localhost:8001`, pick a model, send a message, confirm a real reply comes
   back and that switching models mid-conversation works.
4. Check the configured Logfire project for the resulting trace (agent run span, model
   request span, and host resource metrics).

## Addendum: Final Whole-Branch Review Corrections

The final review (after all 12 tasks landed) found several real cross-task defects
that no single task-scoped review could see — see the branch's git history for the
fix commits. Corrections to this plan's earlier text, for anyone reading it later:

- **`LOGFIRE_PROJECT` does not exist as a Logfire concept** — `logfire.configure()`
  has no such env var; a project is derived from the token itself. It has been
  removed from `LogfireSettings`, `.env.example`, and `AGENTS.md`. Wherever this
  plan's task text above still shows `LOGFIRE_PROJECT=` or a `project` field on
  `LogfireSettings`, treat that as superseded.
- **`.env` files are now actually loaded** by each app (via `load_dotenv()` in
  `chat/__init__.py`, not `main.py` as originally planned — `evals/run.py` doesn't
  import `main.py`, so the package `__init__` is the one place covering both
  entrypoints). Earlier task text describing settings as reading only from ambient
  env vars/Docker's `env_file:` is superseded.
- **`configure_logfire()` gained a `token: str | None = None` parameter**, wired
  from `LogfireSettings`, making that settings class load-bearing instead of dead
  code.
- **`uv run pytest` needs `--import-mode=importlib`** (added to root
  `pyproject.toml`) so a second demo's `tests/` package doesn't collide with the
  first's at collection time — a real problem for the whole point of this
  skeleton being copied.
- Per-request Gateway model construction in `chat/main.py` is now cached
  (`_MODEL_CACHE`) instead of rebuilt (and its HTTP client leaked) on every
  message; malformed `model_choice` values now get a 400, not a 500.
- `evals/run.py` now calls `configure_logfire(...)` so real eval runs are traced,
  matching this plan's original claim that they would be.
- The chat eval dataset's judge agent now uses `output_type=float` instead of
  parsing free text, per the fragility `HarnessJudge`'s own task review flagged.

The task text elsewhere in this document was left as originally written (a
historical record of what was planned and learned along the way) rather than
rewritten throughout — this addendum is the pointer to what changed after the
fact.
