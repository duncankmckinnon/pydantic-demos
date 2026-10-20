# Applicant Support Logfire Evaluation Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add managed prompt/model configuration, shared online and offline evaluators, a hosted extensible Logfire dataset, and on-demand model/prompt comparison experiments to the existing applicant-support demo.

**Architecture:** Live chat and experiments share one explicit agent-run path driven by a typed `AgentConfig`. New sessions resolve prompt/model managed variables once; online evaluation wraps live runs, while experiments fetch the hosted dataset and apply the same evaluator instances to isolated seeded databases. FastAPI exposes local-only experiment jobs, and Logfire remains the UI for case curation, experiment comparison, variable promotion, traces, and historical agent evaluation results.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic AI 2.43, Pydantic Evals 2.43, Logfire 4.41 with `variables` and `datasets` extras, SQLite, Jinja2, uv, Pydantic AI Gateway.

**Spec:** `docs/superpowers/specs/2026-10-20-applicant-support-logfire-evals-design.md`

## Global Constraints

- Work in an in-place feature branch based on current `main`; do not create a worktree.
- Preserve the existing untracked `.codex/` directory and unrelated user changes.
- Configure Logfire once, before constructing any Agent or FastAPI app.
- Use `LOGFIRE_TOKEN` only for telemetry and `LOGFIRE_API_KEY` only for managed variables and hosted datasets; never print, read back, or commit either secret.
- The exact Logfire project is `duncan/applicant-support`; the hosted dataset name is `applicant_support_baseline`.
- Managed variable names are `applicant_support_prompt` and `applicant_support_model`.
- Normal chat does not accept a caller-supplied model or prompt. A new session resolves them once and subsequent turns keep that configuration.
- This local demo runs every online evaluator on every agent call; the capability dispatches them in the background after the response is available.
- Experiments run the full shared evaluator set against hosted cases and use a fresh seeded SQLite database for every case.
- Tests use `TestModel` or `FunctionModel`, disable online evaluation, send no telemetry, access no hosted dataset, and make no real Gateway calls.
- Real model smoke experiments use only two or three cases. Do not run a full billed comparison without showing the candidate/case count and getting explicit user confirmation.

## Review Focus

- A malformed or unsupported managed model value must fall back to the declared safe default instead of routing an arbitrary Gateway model; Task 1 tests this.
- A resumed chat session must not silently switch prompt/model after a managed-variable promotion, while a new session must pick up the promotion; Task 2 tests both sides.
- Experiment runs must not trigger the online-evaluation capability in addition to dataset evaluators; Task 3 and Task 5 test capability separation.
- A seed sync must not delete or overwrite a differently named trace-derived hosted case; Task 4 tests the exact client calls and conflict behavior.
- Rescheduling in one experiment case or candidate must not mutate another case's database; Task 5 tests per-case isolation.

---

### Task 1: Typed managed prompt and model configuration

**Files:**
- Create: `apps/applicant-support/src/applicant_support/config.py`
- Modify: `apps/applicant-support/pyproject.toml`
- Modify: `apps/applicant-support/.env.example`
- Test: `apps/applicant-support/tests/test_config.py`

**Interfaces:**
- Produces: `AgentConfig(BaseModel)` with `api_format: Literal["anthropic", "openai-responses"]`, `model_name: str`, and a `model_choice` property.
- Produces: `ResolvedAgentConfig(BaseModel)` with `prompt`, `model`, `prompt_fingerprint`, and resolution-source fields suitable for trace metadata.
- Produces: `DEFAULT_PROMPT`, `DEFAULT_MODEL`, `ALLOWED_MODELS`, `resolve_agent_config() -> ResolvedAgentConfig`.
- Declares Logfire variables named `applicant_support_prompt` and `applicant_support_model` with defaults matching current behavior.

- [ ] **Step 1: Add failing managed-configuration tests**

  In `test_config.py`, use fake variable objects whose `.get()` methods are context managers. Assert that `resolve_agent_config()` returns both resolved values, computes a stable prompt fingerprint without exposing the full prompt in metadata, and falls back to `DEFAULT_MODEL` for a syntactically valid but non-allowlisted model.

- [ ] **Step 2: Run the tests to verify RED**

  Run: `uv run pytest apps/applicant-support/tests/test_config.py -v`

  Expected: FAIL because `applicant_support.config` does not exist.

- [ ] **Step 3: Add the Logfire extras and configuration module**

  Add `logfire[variables,datasets]` to the app dependencies. Implement the exact interfaces above, with the current hard-coded instruction and first current model choice as safety defaults. Include the two intended experiment families in `ALLOWED_MODELS`; verify their exact enabled Gateway route IDs during Task 7 rather than accepting arbitrary strings.

- [ ] **Step 4: Document credential names without secrets**

  Add `LOGFIRE_API_KEY=` and `LOGFIRE_BASE_URL=https://logfire-us.pydantic.dev` to `.env.example`, explaining that the API key needs `project:read_variables`, `project:read_datasets`, and `project:write_datasets` while `LOGFIRE_TOKEN` remains the telemetry credential.

- [ ] **Step 5: Sync dependencies and verify GREEN**

  Run: `uv sync --all-packages`

  Run: `uv run pytest apps/applicant-support/tests/test_config.py -v`

  Expected: dependency sync succeeds and all configuration tests pass.

- [ ] **Step 6: Commit**

  Commit message: `feat: add managed applicant agent config`

### Task 2: Shared agent-run path and session-stable configuration

**Files:**
- Create: `apps/applicant-support/src/applicant_support/service.py`
- Modify: `apps/applicant-support/src/applicant_support/agent.py`
- Modify: `apps/applicant-support/src/applicant_support/main.py`
- Modify: `apps/applicant-support/tests/conftest.py`
- Modify: `apps/applicant-support/tests/test_agent.py`
- Modify: `apps/applicant-support/tests/test_main.py`

**Interfaces:**
- Changes: `build_agent(settings, *, capabilities: Sequence[AgentCapability] = ()) -> Agent[AgentDeps, str]` keeps tool registration independent of live-only capabilities.
- Produces: `run_applicant_turn(agent, *, question, deps, config, history=(), metadata=None) -> AgentRunResult[str]`, passing `model`, `instructions`, history, and metadata explicitly.
- Changes: the session record stores `application_id`, messages, and one `ResolvedAgentConfig` instead of a tuple.
- Produces: `DELETE /api/session` to start a new local conversation.
- Changes: `POST /api/chat` accepts `application_id` and `message`; it no longer accepts `model_choice` or an online-evaluation sampling override.
- Changes: `ChatResponse` includes `reply`, `model_name`, and `prompt_fingerprint`.

- [ ] **Step 1: Add failing agent-service tests**

  Test with `FunctionModel` that `run_applicant_turn()` supplies the configured instructions and model, forwards prior messages, and adds application ID, resolved model, prompt fingerprint, and session ID to run metadata without storing the prompt text there.

- [ ] **Step 2: Run the service tests to verify RED**

  Run: `uv run pytest apps/applicant-support/tests/test_agent.py -v`

  Expected: FAIL because the new service interface and capability parameter do not exist.

- [ ] **Step 3: Implement the shared runner and capability-aware agent factory**

  Move per-run model/instruction selection into `service.py`. Keep the three existing tools and `AgentDeps` unchanged; the live app and experiments will supply different capability sequences to the same factory.

- [ ] **Step 4: Add failing session tests**

  Replace model-selector endpoint tests with tests proving: the first request resolves config once; a second request in the same cookie session reuses it even when fake variable values change; `DELETE /api/session` causes the next request to resolve the changed config; changing application ID starts fresh history but keeps the current session config only if the cookie remains valid; invalid cookies cannot select another client's state.

- [ ] **Step 5: Run the endpoint tests to verify RED**

  Run: `uv run pytest apps/applicant-support/tests/test_main.py -v`

  Expected: FAIL because requests still require `model_choice` and there is no session reset endpoint or configuration metadata response.

- [ ] **Step 6: Implement session-stable managed configuration**

  Resolve variables only when creating a session record, cache Gateway `Model` instances by validated `model_choice`, invoke `run_applicant_turn()`, return the visible model/fingerprint, and implement `DELETE /api/session`. Preserve applicant-history isolation and safe cookie behavior.

- [ ] **Step 7: Verify GREEN**

  Run: `uv run pytest apps/applicant-support/tests/test_agent.py apps/applicant-support/tests/test_main.py -v`

  Expected: all agent and endpoint tests pass without network access.

- [ ] **Step 8: Commit**

  Commit message: `feat: resolve agent config per session`

### Task 3: Shared evaluator suite and online evaluation capability

**Files:**
- Create: `apps/applicant-support/src/applicant_support/evals/evaluators.py`
- Create: `apps/applicant-support/src/applicant_support/evals/models.py`
- Create: `apps/applicant-support/src/applicant_support/evals/online.py`
- Modify: `apps/applicant-support/src/applicant_support/main.py`
- Create: `apps/applicant-support/tests/test_evaluators.py`
- Modify: `apps/applicant-support/tests/test_evals.py`
- Modify: `apps/applicant-support/tests/test_main.py`

**Interfaces:**
- Produces: `ReplyPresent`, `ToolUseAppropriateness`, `GroundedCorrectness`, `Professionalism`, and `FrustrationHandling` evaluator classes.
- Produces: `ApplicantSupportEvalOutput(BaseModel)` with `reply: str`, the object-shaped
  experiment output shared with hosted expected outputs.
- Produces: `SHARED_EVALUATORS`, containing the exact instances used by experiments.
- Produces: helpers that normalize live string outputs and experiment output objects, extract distinct tool names, and extract tool-return evidence from a `span_tree`.
- Produces: `APPLICANT_SUPPORT_ONLINE_EVALUATION`, wrapping every shared evaluator in an `OnlineEvaluator` with `sample_rate=1.0`.

- [ ] **Step 1: Add failing evaluator tests**

  Use fake span trees plus `FunctionModel` judge agents. Assert: `ReplyPresent` rejects whitespace; lookup questions with no `get_application_status` call produce `missed_opportunity`; unnecessary tools are reported; grounded correctness includes actual tool returns in the judge prompt; professionalism and frustration judgments preserve structured reasons; string and `ApplicantSupportEvalOutput` values normalize identically.

- [ ] **Step 2: Run evaluator tests to verify RED**

  Run: `uv run pytest apps/applicant-support/tests/test_evaluators.py -v`

  Expected: FAIL because the evaluator module does not exist.

- [ ] **Step 3: Implement shared evaluator definitions**

  Follow the existing `apps/chat/src/chat/evals/` span-inspection and typed-judge pattern. Construct judge agents without making network calls at import time, use `anthropic:claude-sonnet-5` as the stable judge model rather than either small candidate model, and return `EvaluationReason` or numeric/boolean values with meaningful names and reasons.

- [ ] **Step 4: Add failing online-capability tests**

  Assert that the online capability wraps the exact shared instances, selects every evaluator for every live call, and `pydantic_evals.online.configure(enabled=False)` in `conftest.py` prevents background real-model activity during endpoint tests.

- [ ] **Step 5: Run online tests to verify RED**

  Run: `uv run pytest apps/applicant-support/tests/test_evals.py apps/applicant-support/tests/test_main.py -v`

  Expected: FAIL because the live agent has no online capability and the sampling policy is absent.

- [ ] **Step 6: Attach online evaluation only to the live agent**

  Build the app's agent with `[APPLICANT_SUPPORT_ONLINE_EVALUATION]`. Keep experiment agents capability-free so dataset evaluation is the only scoring path during experiments.

- [ ] **Step 7: Verify GREEN**

  Run: `uv run pytest apps/applicant-support/tests/test_evaluators.py apps/applicant-support/tests/test_evals.py apps/applicant-support/tests/test_main.py -v`

  Expected: all evaluator and endpoint tests pass offline.

- [ ] **Step 8: Commit**

  Commit message: `feat: add applicant support online evals`

### Task 4: Hosted extensible dataset and seed synchronization

**Files:**
- Rewrite: `apps/applicant-support/src/applicant_support/evals/dataset.py`
- Create: `apps/applicant-support/src/applicant_support/evals/hosted.py`
- Create: `apps/applicant-support/src/applicant_support/evals/sync.py`
- Modify: `apps/applicant-support/tests/test_evals.py`
- Create: `apps/applicant-support/tests/test_hosted_dataset.py`

**Interfaces:**
- Produces: `ApplicantSupportEvalInput` and `ApplicantSupportCaseMetadata` Pydantic models; consumes `ApplicantSupportEvalOutput` from Task 3 so every hosted input, expected output, and metadata value has a JSON-object root.
- Produces: `build_seed_dataset() -> Dataset[ApplicantSupportEvalInput, ApplicantSupportEvalOutput, ApplicantSupportCaseMetadata]` named `applicant_support_baseline` and configured with `SHARED_EVALUATORS`.
- Produces: `sync_seed_dataset(client: LogfireAPIClient) -> DatasetDetail`, calling `push_dataset(..., on_case_conflict="update")`.
- Produces: `load_hosted_dataset(client: LogfireAPIClient) -> Dataset[...]`, fetching typed cases by the stable name and applying `SHARED_EVALUATORS` in code.
- Produces: `python -m applicant_support.evals.sync` as an explicit manual sync command.

- [ ] **Step 1: Add failing typed-dataset tests**

  Assert that every seed case has object-shaped input/output/metadata, unique stable names, complexity/provenance tags, and an expected required tool in metadata where applicable. Add a named lookup-tool regression case whose expected tool is `get_application_status` and whose expected `ApplicantSupportEvalOutput.reply` rejects an unhelpful refusal.

- [ ] **Step 2: Run dataset tests to verify RED**

  Run: `uv run pytest apps/applicant-support/tests/test_evals.py -v`

  Expected: FAIL because the current expected outputs are strings and metadata is an untyped dict.

- [ ] **Step 3: Implement the typed seed dataset**

  Preserve the existing eight scenarios, add only the explicit lookup-tool failure case, and attach `SHARED_EVALUATORS` at dataset level. Keep the stable dataset name unchanged.

- [ ] **Step 4: Add failing hosted-client tests**

  With a fake `LogfireAPIClient`, assert that sync passes the exact stable name and `on_case_conflict="update"`; loading passes all three model types, returns hosted cases including an extra trace-derived case, and replaces/sets dataset evaluators with `SHARED_EVALUATORS` without pushing during ordinary app startup.

- [ ] **Step 5: Run hosted tests to verify RED**

  Run: `uv run pytest apps/applicant-support/tests/test_hosted_dataset.py -v`

  Expected: FAIL because hosted dataset helpers do not exist.

- [ ] **Step 6: Implement hosted sync/load and CLI**

  Construct `LogfireAPIClient` from `LOGFIRE_API_KEY` without logging it. The sync command must print only dataset name/ID/case count and the named-case replacement warning. Do not call sync from FastAPI startup.

- [ ] **Step 7: Verify GREEN**

  Run: `uv run pytest apps/applicant-support/tests/test_evals.py apps/applicant-support/tests/test_hosted_dataset.py -v`

  Expected: all dataset tests pass with a fake client and no network access.

- [ ] **Step 8: Commit**

  Commit message: `feat: add hosted applicant eval dataset`

### Task 5: Controlled model and prompt experiment runner

**Files:**
- Create: `apps/applicant-support/src/applicant_support/evals/experiments.py`
- Rewrite: `apps/applicant-support/src/applicant_support/evals/run.py`
- Create: `apps/applicant-support/tests/test_experiments.py`

**Interfaces:**
- Produces: `ExperimentKind = Literal["model", "prompt"]`, `ExperimentCandidate`, `ExperimentRequest`, and `ExperimentRunSummary` models.
- Produces: `MODEL_CANDIDATES` and `PROMPT_CANDIDATES`; the promoted managed value is included as the control and candidate values never mutate managed variables.
- Produces: `run_comparison(request, *, dataset, gateway_settings) -> list[ExperimentRunSummary]`.
- Produces: `run_smoke_comparison(kind, ...)` using the first two or three hosted cases, and `run_full_comparison(kind, ...)` using all hosted cases.
- Changes: `python -m applicant_support.evals.run --kind model|prompt --smoke|--full` calls the same runner used by the UI.

- [ ] **Step 1: Add failing experiment tests**

  With fake datasets/models, assert: model comparison holds prompt fixed; prompt comparison holds model fixed; every candidate uses the same hosted case names and `SHARED_EVALUATORS`; experiment name/task metadata include comparison group, candidate, model, prompt fingerprint, dataset, and judge model; experiment agents have no online capability.

- [ ] **Step 2: Add failing database-isolation test**

  Run two synthetic rescheduling cases/candidates through the task factory and assert each begins with the original `2026-10-23T10:00:00-07:00` interview time. No candidate may reuse `apps/applicant-support/data/evals.sqlite3`.

- [ ] **Step 3: Run experiment tests to verify RED**

  Run: `uv run pytest apps/applicant-support/tests/test_experiments.py -v`

  Expected: FAIL because the comparison runner does not exist and the old runner shares one mutable database.

- [ ] **Step 4: Implement comparison variants and isolated task factory**

  Fetch/receive the hosted dataset once per job, create a fresh temporary seeded SQLite path per case, run `run_applicant_turn()` with the explicit candidate config, wrap replies in `ApplicantSupportEvalOutput`, and call `Dataset.evaluate()` with stable dataset name plus descriptive experiment `name`, `task_name`, and metadata.

- [ ] **Step 5: Implement smoke/full CLI guards**

  Default the CLI to smoke. Require `--full` explicitly, print candidate and case counts before starting, and never display credentials or prompt bodies.

- [ ] **Step 6: Verify GREEN**

  Run: `uv run pytest apps/applicant-support/tests/test_experiments.py -v`

  Expected: all comparison and isolation tests pass offline.

- [ ] **Step 7: Commit**

  Commit message: `feat: add applicant eval comparisons`

### Task 6: Local experiment jobs and walkthrough UI

**Files:**
- Create: `apps/applicant-support/src/applicant_support/jobs.py`
- Modify: `apps/applicant-support/src/applicant_support/main.py`
- Modify: `apps/applicant-support/src/applicant_support/templates/index.html`
- Modify: `apps/applicant-support/tests/test_main.py`
- Create: `apps/applicant-support/tests/test_jobs.py`

**Interfaces:**
- Produces: in-memory `ExperimentJob` records with `queued|running|completed|failed`, timestamps, kind, scope, candidate/case counts, summaries, and safe error text.
- Produces: `POST /api/experiments/{kind}/smoke`, `POST /api/experiments/{kind}/full`, and `GET /api/experiments/jobs/{job_id}`.
- Requires: a successful smoke job ID of the same kind before accepting a full launch request.
- Changes: the chat UI shows resolved model and prompt fingerprint, a New session action, an always-on online-evaluation status, and model/prompt experiment controls with job status and Logfire links.

- [ ] **Step 1: Add failing job-registry tests**

  Assert legal state transitions, unknown job handling, safe exception capture, and that stored failures omit secrets and full prompts.

- [ ] **Step 2: Run job tests to verify RED**

  Run: `uv run pytest apps/applicant-support/tests/test_jobs.py -v`

  Expected: FAIL because the job module does not exist.

- [ ] **Step 3: Implement the in-memory job registry**

  Use process-local `asyncio.create_task` orchestration appropriate to this local demo. Provide reset hooks for tests; do not add a production queue or persistence layer.

- [ ] **Step 4: Add failing API and rendered-UI tests**

  Assert smoke endpoints enqueue the correct comparison, full endpoints reject missing/failed/mismatched smoke IDs, job status is serializable, session reset works, the old model selector is absent, and the new configuration/experiment controls are present.

- [ ] **Step 5: Run endpoint/UI tests to verify RED**

  Run: `uv run pytest apps/applicant-support/tests/test_main.py -v`

  Expected: FAIL because the experiment routes and walkthrough controls do not exist.

- [ ] **Step 6: Implement experiment endpoints and UI**

  Keep dataset editing, trace capture, run comparison, and variable promotion in Logfire. The local UI only launches prepared code experiments, polls status, links to Logfire, and exposes the chat/session controls required by the walkthrough.

- [ ] **Step 7: Verify GREEN**

  Run: `uv run pytest apps/applicant-support/tests/test_jobs.py apps/applicant-support/tests/test_main.py -v`

  Expected: all job, endpoint, and HTML behavior tests pass offline.

- [ ] **Step 8: Commit**

  Commit message: `feat: add applicant eval demo controls`

### Task 7: Documentation, Logfire resources, and end-to-end verification

**Files:**
- Modify: `apps/applicant-support/README.md`
- Modify: `apps/applicant-support/.env.example`
- Modify: `apps/applicant-support/Dockerfile` if dependency installation needs no existing change beyond the lockfile
- Modify: `docs/superpowers/specs/2026-10-20-applicant-support-logfire-evals-design.md` only for implementation-discovered corrections
- Test: full repository and real integration checks

**Interfaces:**
- Creates in `duncan/applicant-support`: managed variables `applicant_support_prompt` and `applicant_support_model`, each with an initial served version.
- Creates/synchronizes: hosted dataset `applicant_support_baseline` with seed cases and schemas.
- Documents: credential scopes, local/Docker startup, seed sync, smoke/full CLI commands, UI walkthrough, trace-to-dataset steps, experiment comparison, and manual variable promotion.

- [ ] **Step 1: Add/update README verification assertions**

  Extend existing structural tests or add a focused README test only where it protects commands/names from drift: stable project/dataset/variable names, separate credential roles, smoke-before-full warning, and the exact manual trace-to-dataset flow.

- [ ] **Step 2: Update the README and environment documentation**

  Document setup and the demo walkthrough without embedding secrets. State that Logfire experiments run from the app/CLI while Logfire manages cases and reviews/comparisons.

- [ ] **Step 3: Run formatting/static checks and the full offline suite**

  Run: `uv run pytest`

  Run: `git diff --check`

  Expected: the full repository suite passes and the diff has no whitespace errors.

- [ ] **Step 4: Create the two managed variables through Logfire MCP**

  Use `variable_manage` in exact project `applicant-support`. Create each variable if absent, assign its JSON schema/description, and create the initial version matching the code defaults. Read them back with `variable_resolve`; do not create duplicate names or update unrelated variables.

- [ ] **Step 5: Obtain the project-scoped API key out of band if still unavailable through MCP**

  The key must target `duncan/applicant-support` and include `project:read_variables`, `project:read_datasets`, and `project:write_datasets`. Pause for the user to create/download it if the current MCP still lacks an existing-project key-creation tool; never ask them to paste it into chat.

- [ ] **Step 6: Sync and verify the hosted dataset**

  Run: `uv run --package applicant-support python -m applicant_support.evals.sync`

  Verify through the SDK or Logfire UI that `applicant_support_baseline` has the seed cases and schemas. Add or preserve one separately named trace-derived case to prove a later seed sync does not delete it.

- [ ] **Step 7: Verify one managed-variable promotion against live chat**

  Start the app, send a real request, change one variable through MCP/UI, start a new session without restarting the app, and confirm the new trace resolves the new version while the old session remains stable. Restore the intended default afterward if the change was only diagnostic.

- [ ] **Step 8: Run and verify paid smoke comparisons**

  Run two or three cases for the model comparison and prompt comparison. Query Logfire for the matching `evaluate applicant_support_baseline` experiment roots; confirm completion, case count, evaluator mix, task errors, and direct experiment links. If either smoke run fails, fix and repeat before offering a full run.

- [ ] **Step 9: Ask before full comparisons**

  Report the hosted case count, candidate count, judge evaluators, and resulting model-call scale. Run full comparisons only after the user explicitly confirms the billed scope.

- [ ] **Step 10: Verify Agents and Live Evaluations**

  Generate a tagged demo conversation and query `duncan/applicant-support` for the named `applicant_support_agent` `invoke_agent` span, tool spans, managed-variable resolutions, and online evaluation events. Confirm real evidence rather than inferring success from configuration.

- [ ] **Step 11: Commit**

  Commit message: `docs: document applicant eval workflow`

### Task 8: Final review and branch handoff

**Files:**
- Review: all files changed by Tasks 1-7

**Interfaces:**
- Produces: a tested feature branch ready for PR or local integration.

- [ ] **Step 1: Run targeted and full verification again**

  Run: `uv run pytest apps/applicant-support/ -v`

  Run: `uv run pytest`

  Run: `git diff --check`

  Expected: all commands pass with no real model calls from pytest.

- [ ] **Step 2: Review the complete diff against the design**

  Confirm every design outcome is implemented, no credentials or local database files are tracked, normal chat has one managed source of truth, shared evaluator instances are not duplicated, and the hosted dataset remains extensible from Logfire.

- [ ] **Step 3: Request an independent whole-branch code review**

  Give the reviewer the design, this plan, diff base, and verification evidence. Address correctness findings and rerun the affected tests plus the full suite.

- [ ] **Step 4: Present integration options**

  Use `superpowers:finishing-a-development-branch` to offer merge, push/PR, keep, or discard options without performing an unrequested destructive action.
