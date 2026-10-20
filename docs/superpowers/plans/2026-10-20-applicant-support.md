# Applicant Support Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local HR applicant-support chatbot with seeded applicant data, Pydantic AI tools, a basic UI, and an initial offline eval dataset.

**Architecture:** A new `apps/applicant-support` FastAPI package owns a small SQLite repository and a Pydantic AI agent whose run dependency carries the selected application ID and database. FastAPI serves a dependency-free chat page and keeps conversational history in memory; SQLite persists demo application and interview changes.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic AI, Pydantic Evals, SQLite, Jinja2, Logfire, uv, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-10-20-applicant-support-design.md`

## Global Constraints

- The baseline agent instruction remains deliberately short and generic.
- Unit tests use `TestModel` or `FunctionModel`; they never call a real model.
- Tests force dummy Gateway and Logfire credentials, disable Logfire sending, and disable online evals.
- Configure Logfire before constructing any Agent or FastAPI app.
- The app uses its own `.env`; Compose host-port interpolation uses the root `.env`.
- Do not add Pydantic AI Harness or online evals in this increment.

## Review Focus

- An unknown application ID returns a safe not-found result rather than another applicant's data; test in Task 1.
- Rescheduling an application with no scheduled interview does not create one silently; test in Task 1.
- A malformed interview timestamp is rejected by the API before model execution; test in Task 3.
- A closed role is returned as closed and is not presented as open; test in Task 1.
- A forged or malformed session cookie cannot select arbitrary server-side state; test in Task 3.

---

### Task 1: Seeded applicant database

**Files:**
- Create: `apps/applicant-support/src/applicant_support/db.py`
- Test: `apps/applicant-support/tests/test_db.py`

**Interfaces:**
- Produces: `ApplicantDatabase(path: Path | str)`, `initialize() -> None`, `get_application(application_id: str) -> ApplicationRecord | None`, `reschedule_interview(application_id: str, starts_at: datetime) -> InterviewRecord | None`, and `search_roles(query: str) -> list[RoleRecord]`.
- Produces: immutable Pydantic or dataclass records suitable for Pydantic AI tool return values.

- [ ] **Step 1: Write failing repository tests**

  Cover idempotent initialization, seeded status lookup, successful rescheduling, no-interview rescheduling, open and closed role search, and unknown application isolation using a temporary SQLite path.

- [ ] **Step 2: Run tests to verify RED**

  Run: `uv run pytest apps/applicant-support/tests/test_db.py -v`

  Expected: collection fails because `applicant_support.db` does not exist.

- [ ] **Step 3: Implement the SQLite repository and seed data**

  Use explicit tables for candidates, roles, applications, and interviews; foreign keys on; parameterized SQL only; and `INSERT OR IGNORE` seed statements so repeated startup is safe.

- [ ] **Step 4: Run tests to verify GREEN**

  Run: `uv run pytest apps/applicant-support/tests/test_db.py -v`

  Expected: all database tests pass.

- [ ] **Step 5: Commit**

  Commit message: `feat: add seeded applicant database`

### Task 2: Baseline Pydantic AI agent and tools

**Files:**
- Create: `apps/applicant-support/src/applicant_support/agent.py`
- Test: `apps/applicant-support/tests/test_agent.py`

**Interfaces:**
- Consumes: `ApplicantDatabase` and its record/query methods from Task 1.
- Produces: `AgentDeps(database: ApplicantDatabase, application_id: str)` and `build_agent(settings: GatewaySettings) -> Agent[AgentDeps, str]`.
- Produces tools named `get_application_status`, `reschedule_screening_call`, and `search_roles`.

- [ ] **Step 1: Write failing agent tests**

  Assert model choices are well formed, the instruction stays intentionally minimal, and `TestModel` can invoke each tool against a temporary seeded database without network access.

- [ ] **Step 2: Run tests to verify RED**

  Run: `uv run pytest apps/applicant-support/tests/test_agent.py -v`

  Expected: collection fails because `applicant_support.agent` does not exist.

- [ ] **Step 3: Implement the agent and typed tools**

  Build the default Gateway model through `get_model`, set `deps_type=AgentDeps`, and keep instructions to a short HR-representative description without detailed policies or workflows.

- [ ] **Step 4: Run tests to verify GREEN**

  Run: `uv run pytest apps/applicant-support/tests/test_agent.py -v`

  Expected: all agent tests pass with tool return values represented in the model output.

- [ ] **Step 5: Commit**

  Commit message: `feat: add applicant support agent tools`

### Task 3: FastAPI chat endpoint and browser UI

**Files:**
- Create: `apps/applicant-support/src/applicant_support/__init__.py`
- Create: `apps/applicant-support/src/applicant_support/main.py`
- Create: `apps/applicant-support/src/applicant_support/templates/index.html`
- Create: `apps/applicant-support/tests/conftest.py`
- Create: `apps/applicant-support/tests/test_main.py`

**Interfaces:**
- Consumes: `build_agent`, `AgentDeps`, and `ApplicantDatabase`.
- Produces: `create_applicant_support_app(send_to_logfire: bool | None = None, database_path: Path | str | None = None) -> FastAPI`.
- Produces: `POST /api/chat` accepting `application_id`, `message`, and `model_choice`, returning `reply`.

- [ ] **Step 1: Write failing endpoint tests**

  Test the rendered application-ID UI, a successful offline model-overridden response, per-cookie history reuse, unknown model rejection, blank/malformed application input rejection, malformed timestamp rejection for the explicit reschedule example payload, and forged-cookie replacement.

- [ ] **Step 2: Run tests to verify RED**

  Run: `uv run pytest apps/applicant-support/tests/test_main.py -v`

  Expected: collection fails because `applicant_support.main` does not exist.

- [ ] **Step 3: Implement app construction, routes, and UI**

  Load the app-local `.env` in `__init__.py`; configure Logfire first; initialize SQLite; construct the agent; validate the configured model choice and application ID; cache models; store histories by UUID cookie; and render a simple portal-style chat screen with seeded application hints.

- [ ] **Step 4: Run tests to verify GREEN**

  Run: `uv run pytest apps/applicant-support/tests/test_main.py -v`

  Expected: all endpoint tests pass.

- [ ] **Step 5: Commit**

  Commit message: `feat: add applicant support chat interface`

### Task 4: Offline eval starter set and app packaging

**Files:**
- Create: `apps/applicant-support/src/applicant_support/evals/__init__.py`
- Create: `apps/applicant-support/src/applicant_support/evals/dataset.py`
- Create: `apps/applicant-support/src/applicant_support/evals/run.py`
- Create: `apps/applicant-support/tests/test_evals.py`
- Create: `apps/applicant-support/pyproject.toml`
- Create: `apps/applicant-support/.env.example`
- Create: `apps/applicant-support/Dockerfile`
- Create: `apps/applicant-support/README.md`
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `build_agent`, `AgentDeps`, and an initialized temporary or local `ApplicantDatabase`.
- Produces: `applicant_support_eval_dataset: Dataset` and a manual `python -m applicant_support.evals.run` entrypoint.

- [ ] **Step 1: Write failing eval structure tests**

  Assert the dataset contains named cases for passed screening, failed/pending screening, rescheduling, open role, closed role, unknown application, and no invented data, with scenario metadata carrying the application ID.

- [ ] **Step 2: Run tests to verify RED**

  Run: `uv run pytest apps/applicant-support/tests/test_evals.py -v`

  Expected: collection fails because `applicant_support.evals.dataset` does not exist.

- [ ] **Step 3: Add the eval dataset, runner, packaging, Compose service, and docs**

  Declare direct dependencies, wire `APPLICANT_SUPPORT_PORT=${APPLICANT_SUPPORT_PORT:-8005}`, ignore app-local SQLite data, document local/Docker/eval commands, and make the runner initialize its database before evaluating.

- [ ] **Step 4: Sync and verify the new demo**

  Run: `uv sync --all-packages`

  Run: `uv run pytest apps/applicant-support/tests/`

  Run: `cp apps/applicant-support/.env.example apps/applicant-support/.env` followed by `docker compose --profile applicant-support config`

  Expected: dependency sync succeeds, all new tests pass, and Compose renders the service on host port 8005.

- [ ] **Step 5: Run the full repository suite**

  Run: `uv run pytest`

  Expected: all repository tests pass; any pre-existing failure is reported by exact test name.

- [ ] **Step 6: Commit**

  Commit message: `feat: add applicant support eval demo`
