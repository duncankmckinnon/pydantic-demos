# Applicant Support

A local HR applicant-support demo for showing a complete Logfire improvement loop:
observe a live agent, inspect online evaluations, curate traces into a hosted dataset,
compare models and prompts, and manually promote the winning configuration through managed
variables. The Logfire project is `duncan/applicant-support`.

The agent uses Pydantic AI tools against seeded SQLite data. One regression case is designed
to expose a weak model or prompt that refuses an application lookup instead of calling
`get_application_status`.

## Sample data

| Application | Applicant | Role | Status |
| --- | --- | --- | --- |
| `APP-1001` | Maya Chen | Product Engineer | Screening passed; call scheduled |
| `APP-1002` | Jordan Lee | Product Designer | Screening pending |
| `APP-1003` | Sam Rivera | Customer Support Specialist | Not selected |

The Product Engineer and Customer Support Specialist roles are open. Product Designer is
closed. Every experiment case gets its own freshly seeded temporary database, so a
rescheduling case cannot change another case or candidate.

## Configure

```bash
cd apps/applicant-support
cp .env.example .env
```

Fill in these app-local values without committing the file:

- `PYDANTIC_AI_GATEWAY_API_KEY` routes agent and evaluator model calls through the Gateway.
- `LOGFIRE_TOKEN` is the write token used only for application telemetry.
- `LOGFIRE_API_KEY` reads managed variables and reads/writes hosted datasets. It must target
  `duncan/applicant-support` and have `project:read_variables`,
  `project:read_datasets`, and `project:write_datasets`.
- `LOGFIRE_BASE_URL` normally remains `https://logfire-us.pydantic.dev`.

The two managed variables are:

- `applicant_support_prompt`: the complete system instruction as a string;
- `applicant_support_model`: an object with `api_format` and `model_name`, for example
  `{"api_format":"openai-responses","model_name":"gpt-5.4-mini"}`.

Missing or invalid managed values fall back to the code defaults. A model must also appear in
the code allowlist; managed variables cannot route an arbitrary model.

## Run locally or with Docker

From the repository root:

```bash
uv run --package applicant-support uvicorn applicant_support.main:app --reload
```

Open <http://localhost:8000>. A new browser session resolves the prompt and model once. The
configuration remains stable for that conversation; use **New session** to resolve a newly
promoted variable value without restarting the app.

For Docker:

```bash
docker compose --profile applicant-support up --build
```

Open <http://localhost:8005>. Set `APPLICANT_SUPPORT_PORT` in the repository-root `.env` to
change the host port. Compose loads credentials from `apps/applicant-support/.env`.

## Hosted dataset

The stable dataset name is `applicant_support_baseline`. Seed it explicitly; ordinary app
startup never pushes or replaces hosted data:

```bash
uv run --package applicant-support python -m applicant_support.evals.sync
```

The sync updates cases with matching seed names and retains differently named hosted cases,
including cases captured from traces. Its input, expected-output, and metadata roots are JSON
objects so trace-derived cases can use the same typed contract.

To add a live interaction manually:

1. Open **Live View** in `duncan/applicant-support` and select the relevant
   `applicant_support_agent` run span.
2. Click the database-plus action and choose the existing `applicant_support_baseline`
   dataset.
3. Review the extracted input, expected output, and metadata. Reshape them to the dataset's
   object schema, give the case a new stable name, and set provenance to `trace`.
4. Save the case. Logfire retains its source trace link for later review.

A later seed sync will not delete that differently named trace-derived case. Logfire manages
the cases and review experience; evaluator definitions remain in code so live and offline
rubrics cannot drift.

## Online evaluations

Every live agent call runs the free `ReplyPresent` check. The shared model judges evaluate:

- tool-use appropriateness;
- grounded correctness against actual tool returns;
- professionalism;
- frustration handling.

Model judges normally sample at 20%. Enable **Full demo evaluations** for a deliberate
walkthrough turn to run all judges and then inspect their `gen_ai.evaluation.result` events
in Live Evaluations and their trends in the Agents view. Unit tests disable online evaluation
globally and never make a Gateway call.

## Model and prompt experiments

Experiments execute in this app or CLI. Logfire stores the resulting experiment traces and
provides the side-by-side review UI; the Logfire UI itself does not execute the task.

Run the two small smoke comparisons first:

```bash
uv run --package applicant-support python -m applicant_support.evals.run --kind model --smoke
uv run --package applicant-support python -m applicant_support.evals.run --kind prompt --smoke
```

The model comparison holds the managed prompt fixed. The prompt comparison holds the managed
model fixed. Both use the same hosted cases and the exact evaluator instances used online.
The currently managed value is included as the control and experiments never mutate managed
variables.

The UI unlocks a full launch only after a completed smoke job of the same comparison kind.
Before confirmation it shows the exact smoke-tested candidate labels, models, prompt
fingerprints, hosted case count, judge count, and estimated model-call total. The full job is
bound to that same candidate configuration and in-memory dataset snapshot. The CLI likewise
requires `--full` explicitly:

```bash
uv run --package applicant-support python -m applicant_support.evals.run --kind model --full
uv run --package applicant-support python -m applicant_support.evals.run --kind prompt --full
```

Full runs make paid candidate and judge-model calls for every hosted case. Confirm candidate
and case counts before launching one. Job state is process-local, but completed experiment
telemetry remains in Logfire.

## Demo walkthrough

1. Start a new UI session, enable Full demo evaluations, and ask a status question plus one
   frustrated follow-up.
2. In Logfire, inspect the agent trace, tool spans, resolved model/fingerprint metadata, and
   evaluation events for each turn.
3. Capture a useful run into `applicant_support_baseline` using the Live View flow above.
4. Run or open the model comparison and compare candidates side by side in Experiments.
5. Run or open the prompt comparison and review the same evaluator set.
6. In Managed Variables, compare `applicant_support_prompt` and
   `applicant_support_model` with the winning experiment. Update them manually when needed.
7. Click **New session**, generate another interaction, and use the Agents and Live
   Evaluations views to show the promoted configuration and evaluation results over time.
