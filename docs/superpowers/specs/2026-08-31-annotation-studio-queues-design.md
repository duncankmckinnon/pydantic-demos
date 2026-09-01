# annotation-studio: Annotation Queues — Design

Date: 2026-08-31
Status: Approved for implementation planning

## Motivation

`annotation-studio` v1 (see `2026-08-28-annotation-studio-design.md`) has exactly one hardcoded
review surface: every interaction where `span_name = 'invoke_agent {top_level_agent_name}'`,
with one shared criteria/label set and one global annotator pool. That's enough to grade
top-level agent turns, but not to build a curated review batch around anything else — a set of
tool calls, a set of evaluation results, a random sample of a specific failure mode, etc. — or
to turn a reviewed batch into a reusable eval dataset.

This adds **annotation queues**: named, independently configured review batches within the
existing project. Each queue owns its own Logfire query, its own criteria/label definition, its
own assigned annotators, and a sampling percentage that controls how much of what the query
matches actually lands in the queue. Queues can also be previewed against Logfire's own SQL
workbench before saving, and a queue's annotated items can be pushed to Logfire as a hosted
dataset for use in evals/experiments.

## Scope

- Multi-project support (creating additional projects, each pointed at a different Logfire
  project) is explicitly **out of scope** for this change. The existing single seeded project
  (`rx-assistant`) remains the only project; this change is entirely about what lives inside it.
- Criteria text, labels, and annotations move from being project-scoped to being **queue**-scoped.
  A project is now just a name plus a list of queues.
- No auth (unchanged repo-wide convention).

## Data Model (SQLite)

This is a breaking schema change. `annotation-studio`'s SQLite file is local, gitignored demo
state with no production deployment — there is no migration path; a local dev database created
against the old schema must be deleted and recreated against the new one.

```sql
CREATE TABLE projects (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE queues (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    name TEXT NOT NULL,
    query TEXT NOT NULL,                     -- full SQL SELECT against `records`
    criteria_text TEXT NOT NULL DEFAULT '',
    sampling_percentage INTEGER NOT NULL DEFAULT 100,  -- 1-100
    last_refreshed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, name)
);

CREATE TABLE labels (
    id INTEGER PRIMARY KEY,
    queue_id INTEGER NOT NULL REFERENCES queues(id),
    name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    UNIQUE(queue_id, name)
);

CREATE TABLE queue_annotators (
    queue_id INTEGER NOT NULL REFERENCES queues(id),
    annotator_id INTEGER NOT NULL REFERENCES annotators(id),
    PRIMARY KEY (queue_id, annotator_id)
);

CREATE TABLE annotators (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE queue_items (
    id INTEGER PRIMARY KEY,
    queue_id INTEGER NOT NULL REFERENCES queues(id),
    trace_id TEXT NOT NULL,
    span_id TEXT NOT NULL,
    start_timestamp TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    UNIQUE(queue_id, trace_id, span_id)
);

CREATE TABLE annotations (
    id INTEGER PRIMARY KEY,
    queue_id INTEGER NOT NULL REFERENCES queues(id),
    trace_id TEXT NOT NULL,
    span_id TEXT NOT NULL,
    annotator_id INTEGER NOT NULL REFERENCES annotators(id),
    label_id INTEGER REFERENCES labels(id),
    description TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL DEFAULT 1,
    writeback_status TEXT NOT NULL DEFAULT 'pending',
    writeback_error TEXT,
    written_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(queue_id, trace_id, span_id, annotator_id)
);
```

- `queue_annotators` is the assigned set. **An empty assigned set means the queue is open to
  every annotator** — this is what lets the seeded default queue work before any annotator
  profile exists, and is the default for a newly created queue until someone deliberately
  restricts it. A non-empty set restricts both which queues an annotator sees in the queue list
  and whether their `annotator_id` may read/annotate the queue's items (HTTP 403 otherwise).
- On first run, the single seeded project gets one seeded queue ("All rx_assistant
  interactions") reproducing today's default behavior: `query` is
  `SELECT trace_id, span_id, start_timestamp, duration, attributes FROM records WHERE
  span_name = 'invoke_agent {top_level_agent_name}' ORDER BY start_timestamp DESC` (the
  configured `top_level_agent_name`, validated the same way as today, interpolated once at seed
  time), starter labels `Pass`/`Neutral`/`Fail`, `sampling_percentage = 100`, no assigned
  annotators (open to all).
- Deleting a queue is an explicit, application-level cascade — one transaction deleting
  `annotations`, then `queue_items`, then `queue_annotators`, then `labels`, then the `queues`
  row itself — not a database-level `ON DELETE CASCADE`. This matches the rest of the app's FK
  style, where `PRAGMA foreign_keys = ON` otherwise enforces `RESTRICT` (e.g. an annotator or
  label referenced by an annotation can't be deleted). Unlike those, deleting a queue is never
  blocked by its own annotations — removing the whole batch, including its annotations, is the
  point.

## Query

`queues.query` is a full, user-authored SQL `SELECT` statement run against Logfire's `records`
table via the existing `AsyncLogfireQueryClient` — not a fixed template with one interpolated
value, and not restricted to a WHERE-clause fragment. This is what lets a queue target agent
turns, tool calls, evaluation-result spans, or anything else expressible as a query over
`records`.

**Validation (defense-in-depth, not the real security boundary — Logfire's query endpoint is
read-only regardless):**
- Must start with `SELECT` (case-insensitive, after stripping leading whitespace/comments).
- No `;` anywhere (blocks stacked statements) — a single trailing `;` is stripped before this
  check rather than rejected, since it's a harmless habit.
- Rejects DDL/DML keywords as whole words (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`,
  `CREATE`, `TRUNCATE`, `GRANT`, `REVOKE`, `MERGE`, `EXEC`, `CALL`).
- Length-capped (5000 chars).
- **On save**, the query is run once wrapped as `SELECT * FROM ({query}) AS q LIMIT 1` to
  confirm it executes and that the result includes `trace_id`, `span_id`, and `start_timestamp`
  columns — required for identity, ordering, and pagination. A Logfire-side error (bad SQL,
  unknown column) or a missing required column is surfaced as a validation error naming what's
  wrong, not saved silently.

**Query helpers**: the queue editor offers starter snippets to insert into the query textarea
rather than starting from a blank page:
1. **Agent turn input/output** (today's default) — `WHERE span_name = 'invoke_agent
   {agent_name}'`.
2. **Tool calls** — `WHERE span_name LIKE 'execute_tool %'`.
3. **Evaluation results** — a generic scaffold targeting eval/score-shaped spans. This one is
   explicitly a starting point, not a verified-correct query against this org's real eval span
   shape (unlike the other two, which mirror real, previously-confirmed trace data) — it's
   expected to be adjusted using the Logfire Explore preview (below) before saving.

**Display**: a queue item's row content comes from whatever the query returns for that
trace/span. The existing heuristic (parse `pydantic_ai.all_messages`/`final_result` out of an
`attributes` column when present) still applies for agent-turn-shaped queries; when that shape
isn't present, the UI shows the full raw row (every returned column, not just `attributes`) as
JSON — so a tool-call or eval-result query isn't forced into an input/output shape that doesn't
fit it, and nothing is silently hidden. Dataset export (below) uses the same fallback.

## Sampling and refresh

- A queue's membership (`queue_items`) is **pull-based and grows over time**, not a live re-query
  on every page view and not a one-time snapshot.
- Opening a queue's detail page or clicking "Refresh" re-runs `queue.query` via
  `AsyncLogfireQueryClient.query_json_rows`, bounded by `min_timestamp = max(last_refreshed_at,
  now - 14d)` (Logfire's max query range) and a fixed row cap per call (1000), and inserts any
  `(trace_id, span_id)` pair not already present in `queue_items` (`INSERT OR IGNORE`, relying on
  the table's `UNIQUE` constraint). `last_refreshed_at` is updated after each call regardless of
  whether new rows were found.
- **Sampling is decided once per item, deterministically**, not re-rolled on refresh:
  `md5(f"{queue_id}:{trace_id}:{span_id}")` mod 100 compared against `sampling_percentage`. Only
  matches that pass are written to `queue_items`; the rest are simply not stored. This is what
  makes "grows over time" safe — an item already shown to a reviewer can never disappear on a
  later refresh, and re-running the same query always makes the same inclusion decision for a
  given trace/span. `sampling_percentage` can be edited after creation, but that only affects
  which *newly discovered* matches get sampled in going forward; existing `queue_items` are never
  retroactively removed or added because of it.
- Creating a queue runs one refresh immediately, so it isn't empty until someone clicks
  "Refresh."
- The queue detail page paginates over local `queue_items` (joined with the selected annotator's
  annotation), not a live Logfire cursor.

## Logfire preview

No in-app query preview/sample-browsing UI is built. Instead, the queue editor and queue detail
page show:
- **"Open in Logfire Explore ↗"** — a link to `{base_url}/{organization_name}/{project_name}/explore`,
  Logfire's own SQL workbench, where the query can actually be run and iterated on against live
  data. The query is also appended as a best-effort `?q=` URL param (the same param
  `build_trace_link` already uses for the live view) in case Explore reads from the same param —
  this isn't a documented contract, so it isn't relied on.
- **"Copy query"** — copies `queue.query` to the clipboard, the reliable way to get it into
  Explore regardless of whether the URL param pre-fills it.

## Dataset creation

Uses `logfire.experimental.api_client.AsyncLogfireAPIClient` (installed with the `logfire`
package already used for read/write access) — a real hosted-datasets API with `create_dataset`,
`add_cases`, and `push_dataset`. Requires a new token scoped `project:write_datasets`
(`RX_ASSISTANT_LOGFIRE_DATASETS_TOKEN`), distinct from the existing read and write tokens.

- Export is scoped to one queue: `POST /api/queues/{id}/datasets` with `{name, label_id?}`.
- Only **annotated** items are included — an unannotated item has no label/description/annotator
  to put in the case metadata described below. `label_id` optionally restricts export to items
  annotated with that specific label (e.g. "only export items labeled Fail").
- An item annotated by more than one annotator produces one case per annotation.
- Each case: `inputs` = the parsed interaction input text (or the raw row, per the Query
  section's fallback), `expected_output` = the parsed output text (or raw row), `metadata` =
  `{label, description, annotator_name, trace_id, span_id}`.
- Building a case requires re-fetching the source row's content from Logfire (a batched query by
  `trace_id`/`span_id`), which inherits the existing 14-day query-range limitation: an item whose
  trace has aged out of that window can't be resolved. Those are skipped, not treated as errors —
  the response reports `{case_count, skipped_count}` so the caller knows some items were dropped
  without the whole export failing.
- `push_dataset` handles create-or-update by name, so re-running an export with the same name
  updates the hosted dataset rather than erroring.

## API

- `GET /api/projects`, `GET /api/projects/{id}`, `PUT /api/projects/{id}` (`{name}` only now —
  `top_level_agent_name`/`criteria_text` moved to queues).
- `GET /api/annotators`, `POST /api/annotators`, `PUT /api/annotators/{id}`,
  `DELETE /api/annotators/{id}` — unchanged.
- `GET /api/projects/{id}/queues?annotator_id=` — list queues, each including `is_accessible`
  (true if unrestricted, or restricted and `annotator_id` is assigned; without `annotator_id`,
  only unrestricted queues report `is_accessible: true`).
- `POST /api/projects/{id}/queues` — create `{name, query, criteria_text, labels,
  sampling_percentage, annotator_ids}`; validates the query (including the save-time Logfire
  dry-run), creates the queue plus labels plus `queue_annotators` in one transaction, then runs
  an initial refresh.
- `GET /api/queues/{id}?annotator_id=` — queue detail (name, query, criteria_text, labels,
  sampling_percentage, assigned annotators, `is_accessible`).
- `PUT /api/queues/{id}` — edit `{name, query, criteria_text, labels, sampling_percentage,
  annotator_ids}`; re-validates the query the same way as create. Does not itself trigger a
  refresh.
- `DELETE /api/queues/{id}` — delete a queue and its cascaded rows.
- `POST /api/queues/{id}/refresh?annotator_id=` — pull new matches per the Sampling and Refresh
  section; 403 if the queue is restricted and `annotator_id` isn't assigned. Returns
  `{new_item_count, total_item_count}`.
- `GET /api/queues/{id}/items?annotator_id=&cursor=` — paginated `queue_items` merged with that
  annotator's annotation, with each page's row content live-fetched from Logfire (batched by
  trace/span ids); a row whose trace has aged out of the 14-day window renders a "trace no longer
  available" placeholder instead of failing the whole page. 403 if restricted and not assigned.
- `PUT /api/queues/{id}/annotations/{trace_id}/{span_id}` — upsert an annotation (same shape as
  today, queue-scoped). 403 if restricted and not assigned.
- `POST /api/queues/{id}/datasets` — `{name, label_id?}`, per the Dataset creation section.
  Returns `{name, case_count, skipped_count}`.

## Frontend

- **Project detail** (`/projects/:id`) — becomes a queue list (name, item count, sampling %,
  restricted/open badge) plus "New queue," replacing today's criteria editor + interaction list.
- **Queue form** (`/projects/:id/queues/new`, `/queues/:id/edit`) — name; query textarea with the
  three helper-snippet buttons, "Open in Logfire Explore ↗," and "Copy query"; criteria textarea;
  label editor (the existing add/rename/remove/reorder-by-stable-id logic, moved here); annotator
  multi-select checkboxes drawn from the global annotator list; sampling percentage input (1-100);
  Save.
- **Queue detail** (`/queues/:id`) — header shows the queue's criteria/labels (link to edit),
  "Refresh" (reports how many new items were pulled in), "Open in Logfire Explore ↗," "Create
  dataset" (opens a small modal: dataset name, optional label filter, submit — reports case/skip
  counts on success). Below: the existing paginated interaction-row UI, generalized so a row
  without a recognizable input/output shape renders its raw JSON instead.
- A queue the selected annotator isn't assigned to shows a clear "you're not assigned to this
  queue" state instead of loading items, mirroring today's "select an annotator first" state.

## Settings

```python
class SourceSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)
    read_token: str = Field(validation_alias="RX_ASSISTANT_LOGFIRE_READ_TOKEN")
    write_token: str = Field(validation_alias="RX_ASSISTANT_LOGFIRE_WRITE_TOKEN")
    datasets_token: str = Field(validation_alias="RX_ASSISTANT_LOGFIRE_DATASETS_TOKEN")
    top_level_agent_name: str = Field(default="rx_assistant_agent")
```

`.env.example` gains:

```
# Publishes queue exports as Logfire hosted datasets (project:write_datasets scope),
# minted separately from the read/write tokens above.
RX_ASSISTANT_LOGFIRE_DATASETS_TOKEN=
```

## Out of Scope

- **Multi-project support** (creating projects beyond the seeded `rx-assistant` one) — deferred
  per this change's scope decision.
- **Retroactive re-sampling.** Editing `sampling_percentage` only affects matches discovered
  after the edit; existing `queue_items` are never added or removed by it.
- **Scheduled/background refresh.** Refresh is triggered by opening a queue or an explicit
  button, never a poller.
- **A verified "evaluation results" query template.** It's a scaffold to be confirmed against
  real eval span data using the Logfire Explore preview, not a guaranteed-correct query.
- **A guaranteed Explore query-prefill contract.** The `?q=` param is best-effort; "Copy query"
  is the reliable path.
- **Migrating existing local annotation data** across the schema change — the local SQLite file
  is recreated, not migrated.
- Everything already out of scope in the v1 design (mutating completed source spans, exactly-once
  write-back delivery, Logfire's native gated annotation-queue feature, non-`rx-assistant-demo`
  projects, an offline evals suite, production deployment/auth/secrets).

## Testing

- `db.py`: queue CRUD; `queue_annotators` assignment and the empty-set-means-open semantics;
  `queue_items` insert/uniqueness/dedupe; annotations now keyed by `queue_id`; cascading delete
  of a queue's labels/queue_annotators/queue_items/annotations.
- `logfire_client.py`: the query validator (rejects non-`SELECT`, semicolons, DDL/DML keywords;
  accepts a valid arbitrary `SELECT`); the save-time dry-run's required-column check; the
  deterministic sampling function (same id always yields the same decision; roughly matches the
  configured percentage across many synthetic trace/span ids); refresh's dedupe against existing
  `queue_items`.
- A new `logfire_datasets.py` module (case-building from annotated queue items, using the same
  parsed/raw fallback as display) and its tests, using a fake `AsyncLogfireAPIClient` — asserts
  correct case shape, correct handling of multiple annotators per item, correct skip-and-report
  behavior for items whose trace has aged out of the query window, and that no real dataset API
  call happens in the default test suite.
- `routes.py`: queue create/edit/delete/refresh/dataset endpoints; 403 enforcement for restricted
  queues; 400s from query validation failures.
- Frontend: unchanged convention from v1 — no component test suite; `npm run build` succeeding is
  the CI-relevant check, manual in-browser verification is the bar for UI behavior.
