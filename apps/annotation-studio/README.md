# Annotation Studio

A review-queue tool for grading traces from *any* Logfire project: define a SQL query over
Logfire's `records` table, assign annotators, grade matching traces against a rubric, and
optionally push the graded set to Logfire as a hosted dataset for evals. It isn't tied to
another demo in this repo — point it at any Logfire project you have access to.

## Configure

Copy `.env.example` to `.env` in this directory and fill in:

| Variable | Purpose | Token to mint |
| --- | --- | --- |
| `LOGFIRE_TOKEN` | This app's own self-instrumentation (its FastAPI/SQLite traces) | Write token for a new, dedicated `annotation-studio` Logfire project — don't reuse one of the tokens below |
| `LOGFIRE_READ_TOKEN` | Reads spans from the Logfire project you want to annotate | Read token, scoped to that project |
| `LOGFIRE_WRITE_TOKEN` | Appends annotation events back into that same project | Write token, scoped to that project (distinct from `LOGFIRE_TOKEN` above) |
| `LOGFIRE_DATASETS_TOKEN` | Publishes a queue's graded items as a Logfire hosted dataset | Token with the `project:write_datasets` scope, scoped to that project |

All four are minted from the relevant project's own Settings page in Logfire — the exact
navigation may vary, so look for "write tokens" / "read tokens" / API keys with selectable
scopes. `LOGFIRE_READ_TOKEN`, `LOGFIRE_WRITE_TOKEN`, and `LOGFIRE_DATASETS_TOKEN` all point at
the *same* project (the one you're annotating); `LOGFIRE_TOKEN` points at a different one
(this app's own).

`LOGFIRE_TOKEN` must still be set to *some* value even if you don't want this app's own
telemetry sent anywhere — Logfire requires the variable to be present. Set
`LOGFIRE_SEND_TO_LOGFIRE=false` and `LOGFIRE_TOKEN` can be any placeholder string in that
case. `LOGFIRE_READ_TOKEN`, `LOGFIRE_WRITE_TOKEN`, and `LOGFIRE_DATASETS_TOKEN` have no such
escape hatch — every feature depends on them, so they must be real tokens.

`ANNOTATION_STUDIO_DATABASE_PATH` (local SQLite path) has a working default and doesn't need
to be set for the Docker workflow below — data persists in the `annotation_studio_data`
Docker volume instead.

## Run

This app's frontend is built into the Docker image, so Docker is the simplest way to run it —
there's no separate host/`uvicorn --reload` workflow for this demo. From the repo root:

```bash
docker compose --profile annotation-studio up --build -d
```

Open http://localhost:8003.

## Set up your first annotation queue

1. On first open you're prompted to create an annotator profile — just a name, used to
   attribute grades and to scope which queues you can see.
2. You land on your one project, automatically named after the real Logfire project
   `LOGFIRE_READ_TOKEN` points at. It starts with zero queues.
3. Click **+ New queue**. Write a SQL query against Logfire's `records` table — the query
   helper buttons give you a starting point for agent turns, tool calls, or eval results — set
   grading criteria and labels, a sampling percentage, and which annotators can see it (leave
   empty to open it to everyone).
4. On the queue's page, click **Refresh** to pull matching traces from Logfire, then grade
   them against your criteria.
5. Once some items are graded, **Create dataset** pushes them to Logfire as a hosted dataset
   for evals.
