# Ruby OpenTelemetry Collector Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Sinatra demo that exports traces, logs, and metrics by OTLP/HTTP to a dedicated OpenTelemetry Collector, which alone holds the credentials needed to forward them to Logfire.

**Architecture:** A small Ruby service owns HTTP behavior and a focused telemetry adapter built on the official OpenTelemetry Ruby APIs. Docker Compose gives the app only a Collector endpoint, while a dedicated Collector receives all three OTLP signals and forwards them to the configured Logfire region and project.

**Tech Stack:** Ruby 3.3+, Sinatra, Puma, Minitest, Rack::Test, official OpenTelemetry Ruby SDK/exporter/instrumentation gems, OpenTelemetry Collector Contrib, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-10-21-ruby-otel-collector-demo-design.md`

## Global Constraints

- The Ruby process sends OTLP/HTTP only to `http://otel-collector-ruby-otel:4318`; it never receives `LOGFIRE_TOKEN` or `LOGFIRE_BASE_URL`.
- Use `service.name=ruby-otel-demo`, `service.version=0.1.0`, and `deployment.environment.name=local`.
- Provide `GET /health`, `GET /`, `GET /work`, and `GET /error`; the error response must be sanitized JSON with status `500`.
- Send no secrets, request bodies, stack traces, or high-cardinality identifiers as telemetry attributes.
- Pin a concrete Ruby image, gem lockfile, and Collector Contrib image version.
- Ruby trace support is stable; its metrics and logs implementations are development-status and must be described that way.
- Default tests must be offline and must not use a real Logfire token or send telemetry outside the test process.
- This app intentionally has no `pyproject.toml`, `demo_core`, Gateway key, Pydantic AI agent, or eval suite.

## Review Focus

- A missing Collector at app startup must not prevent the Sinatra process from starting; the exporter may retry and report locally.
- `/error` must return only the fixed public error payload while the recorded span receives the exception and error status.
- A missing or blank `LOGFIRE_TOKEN` must make the real Collector configuration fail clearly rather than silently exporting unauthenticated data.
- Route/outcome metric attributes must remain bounded to the documented values and never include raw URLs or exception messages.
- Process shutdown must force-flush all configured providers so a final request in a live demo is exported.

---

### Task 1: Sinatra Application Contract

**Files:**
- Create: `apps/ruby-otel/Gemfile`
- Create: `apps/ruby-otel/Gemfile.lock`
- Create: `apps/ruby-otel/app.rb`
- Create: `apps/ruby-otel/test/test_helper.rb`
- Create: `apps/ruby-otel/test/app_test.rb`

**Interfaces:**
- Consumes: no earlier task interfaces.
- Produces: `RubyOtelDemo::App < Sinatra::Base`; an injectable object at `App.settings.telemetry` implementing `in_span(name, attributes:)`, `log(severity:, body:, attributes:)`, `record_request(route:, outcome:)`, and `record_exception(exception)`.

- [ ] **Step 1: Write failing route contract tests**

In `test/app_test.rb`, use Rack::Test and a recording fake telemetry adapter. Add tests named:

- `test_root_lists_demo_routes`: asserts `200`, JSON content type, and the exact route list `['/work', '/error', '/health']`.
- `test_health_is_ready_without_emitting_demo_telemetry`: asserts `200`, `{"status":"ok"}`, and no fake adapter calls.
- `test_work_emits_child_span_log_and_success_metric`: asserts `200`, response operation `ruby-otel.work`, and exactly one call each to `in_span('ruby-otel.work')`, `log` with body `ruby work completed`, and `record_request(route: '/work', outcome: 'success')`.
- `test_error_records_exception_and_returns_sanitized_payload`: asserts `500`, the fixed response `{"error":"intentional demo failure","operation":"ruby-otel.error"}`, no backtrace/internal exception message in the body, and adapter calls for exception recording, error log, and `record_request(route: '/error', outcome: 'error')`.
- `test_metrics_use_only_bounded_route_and_outcome_values`: calls both demo routes and asserts the complete metric attribute sets are only `{route: '/work', outcome: 'success'}` and `{route: '/error', outcome: 'error'}`.

- [ ] **Step 2: Run the route tests and verify they fail because the app does not exist**

Run: `cd apps/ruby-otel && bundle exec ruby -Itest test/app_test.rb`

Expected: FAIL with a load error for `app.rb` or missing `RubyOtelDemo::App`.

- [ ] **Step 3: Add the minimal Ruby dependencies**

In `Gemfile`, declare Ruby `>= 3.3` and direct dependencies on Sinatra, Puma, Rackup, JSON, Minitest, and Rack::Test. Add the OpenTelemetry dependencies needed by Task 2 now so Bundler resolves one compatible set: API/SDK, OTLP trace/metric/log exporters, metric/log SDKs, and Sinatra/Rack instrumentation. Generate and commit `Gemfile.lock` using the selected Ruby 3.3+ runtime.

- [ ] **Step 4: Implement `RubyOtelDemo::App` and the Rack entry point**

Implement the four routes in `app.rb`, depending only on the adapter interface above. `GET /work` wraps its operation in `telemetry.in_span`; `GET /error` raises a private fixed exception, catches it at the route boundary, calls `record_exception`, emits the bounded error telemetry, and returns the fixed public payload. Configure JSON responses consistently. Keep the adapter injectable without requiring the real telemetry implementation at file-load time.

- [ ] **Step 5: Run route and syntax checks**

Run: `cd apps/ruby-otel && bundle exec ruby -Itest test/app_test.rb && bundle exec ruby -c app.rb`

Expected: all tests pass and each syntax check prints `Syntax OK`.

- [ ] **Step 6: Commit the application contract**

```bash
git add apps/ruby-otel/Gemfile apps/ruby-otel/Gemfile.lock apps/ruby-otel/app.rb apps/ruby-otel/test
git commit -m "feat: add ruby otel demo service"
```

### Task 2: OpenTelemetry Adapter and Offline Signal Export Test

**Files:**
- Create: `apps/ruby-otel/lib/telemetry.rb`
- Create: `apps/ruby-otel/config.ru`
- Create: `apps/ruby-otel/test/telemetry_test.rb`
- Create: `apps/ruby-otel/test/otel_export_test.rb`
- Modify: `apps/ruby-otel/test/test_helper.rb`

**Interfaces:**
- Consumes: the telemetry adapter calls made by `RubyOtelDemo::App` from Task 1.
- Produces: singleton module methods `RubyOtelDemo::Telemetry.start!`, `.shutdown!`, `.in_span(name, attributes: {}) { |span| ... }`, `.log(severity:, body:, attributes: {})`, `.record_request(route:, outcome:)`, and `.record_exception(exception)`.

- [ ] **Step 1: Write failing adapter unit tests with in-memory providers**

In `telemetry_test.rb`, install in-memory/simple processors supplied by the resolved OTel gems and test:

- `test_in_span_creates_named_child_with_attributes` checks span name `ruby-otel.work` and only the supplied stable attributes.
- `test_log_uses_active_trace_context_and_structured_attributes` checks body, severity, attributes, and nonzero trace/span IDs while called inside `in_span`.
- `test_request_counter_adds_one_with_bounded_attributes` checks instrument name `ruby_otel.requests`, unit `{request}`, value `1`, and exact route/outcome attributes.
- `test_record_exception_sets_current_span_error_status` checks the span status and exception event while not exposing the exception message as a custom attribute.
- `test_shutdown_force_flushes_and_is_idempotent` uses recording provider doubles and checks trace, metric, and log flush/shutdown calls once.
- `test_start_does_not_raise_when_otlp_endpoint_is_unavailable` points at a closed loopback port, starts providers, performs one operation, and asserts startup/application execution succeeds before clean shutdown.

- [ ] **Step 2: Run the adapter tests and verify they fail**

Run: `cd apps/ruby-otel && bundle exec ruby -Itest test/telemetry_test.rb`

Expected: FAIL because `lib/telemetry.rb` and its module methods do not exist.

- [ ] **Step 3: Implement provider setup and the adapter methods**

In `lib/telemetry.rb`, load the official OTel SDKs/exporters and configure:

- a batch OTLP span processor plus Sinatra/Rack instrumentation;
- a periodic OTLP metric reader and a counter named `ruby_otel.requests` with unit `{request}`;
- a batch OTLP log processor and logger named `ruby-otel-demo`;
- a resource carrying the three exact service attributes from Global Constraints;
- an `at_exit` hook delegating to idempotent `shutdown!`.

Use standard `OTEL_EXPORTER_OTLP_*` environment handling where the gems support it. Do not read Logfire-specific variables. Keep `.record_exception` restricted to OTel's standard exception event/status behavior.

Create `config.ru` to require telemetry before the app, call `RubyOtelDemo::Telemetry.start!`, assign that adapter to `RubyOtelDemo::App.settings.telemetry`, and run the app.

- [ ] **Step 4: Run the adapter and application tests**

Run: `cd apps/ruby-otel && bundle exec ruby -Itest test/telemetry_test.rb && bundle exec ruby -Itest test/app_test.rb`

Expected: all tests pass without network access.

- [ ] **Step 5: Write the failing loopback OTLP integration test**

In `otel_export_test.rb`, run a loopback-only fake HTTP server on an ephemeral port, set per-signal OTLP endpoints to its `/v1/traces`, `/v1/metrics`, and `/v1/logs` paths, start the real adapter, execute `/work` through Rack::Test, force-flush, and assert that each of the three paths receives at least one protobuf POST. Assert no request contains an `Authorization` header and no host other than loopback is contacted.

- [ ] **Step 6: Run the OTLP integration test and verify the first failure**

Run: `cd apps/ruby-otel && bundle exec ruby -Itest test/otel_export_test.rb`

Expected: FAIL on whichever signal is not yet correctly wired or flushed; if all three pass from Step 3, record that the test passed immediately and do not introduce a deliberate regression.

- [ ] **Step 7: Complete signal wiring until all three OTLP paths pass**

Make the smallest provider/exporter adjustments needed for all three signals. Keep the fake receiver process-local and keep production defaults pointed only by standard OTel environment variables.

- [ ] **Step 8: Run all Ruby tests and dependency validation**

Run: `cd apps/ruby-otel && bundle check && bundle exec ruby -Itest test/app_test.rb && bundle exec ruby -Itest test/telemetry_test.rb && bundle exec ruby -Itest test/otel_export_test.rb`

Expected: `The Gemfile's dependencies are satisfied` and all tests pass.

- [ ] **Step 9: Commit telemetry support**

```bash
git add apps/ruby-otel/lib/telemetry.rb apps/ruby-otel/config.ru apps/ruby-otel/test
git commit -m "feat: export ruby traces logs and metrics over otlp"
```

### Task 3: Collector, Containers, and Compose Boundary

**Files:**
- Create: `apps/ruby-otel/Dockerfile`
- Create: `apps/ruby-otel/Collector.Dockerfile`
- Create: `apps/ruby-otel/bin/start-collector`
- Create: `apps/ruby-otel/.env.example`
- Create: `infra/otel-collector/ruby-otel.yaml`
- Create: `apps/ruby-otel/test/config_test.rb`
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Modify: `.gitignore` if the existing rules do not already ignore `apps/*/.env`

**Interfaces:**
- Consumes: Rack entry point `config.ru`, internal port `9292`, and standard OTel environment variables from Tasks 1–2.
- Produces: Compose services `ruby-otel` and `otel-collector-ruby-otel`; profile `ruby-otel`; host port variable `RUBY_OTEL_PORT`; Collector pipelines for traces, metrics, and logs.

- [ ] **Step 1: Write failing static configuration tests**

In `config_test.rb`, parse Compose and Collector YAML and assert:

- the Ruby service has profiles `ruby-otel` and `all`, maps `${RUBY_OTEL_PORT:-8006}:9292`, and sets `OTEL_EXPORTER_OTLP_ENDPOINT` to `http://otel-collector-ruby-otel:4318`;
- the Ruby service has no `env_file`, `LOGFIRE_TOKEN`, or `LOGFIRE_BASE_URL` entry;
- the Collector alone loads `apps/ruby-otel/.env`, is built from a concrete pinned Contrib tag, and belongs to both profiles;
- the Collector defines OTLP/HTTP on `0.0.0.0:4318`, `memory_limiter`, `batch`, health checking, and active trace/metric/log pipelines exporting through `otlphttp/logfire`;
- the exporter endpoint uses `${env:LOGFIRE_BASE_URL}` and header `Authorization: ${env:LOGFIRE_TOKEN}`;
- the Collector entry point exits nonzero with a clear message when either required variable is blank, before starting the Collector;
- `.env.example` contains only `LOGFIRE_TOKEN` and the US default `LOGFIRE_BASE_URL` for this app;
- the root `.env.example` contains `RUBY_OTEL_PORT=8006`.

- [ ] **Step 2: Run the configuration tests and verify they fail**

Run: `cd apps/ruby-otel && bundle exec ruby -Itest test/config_test.rb`

Expected: FAIL because the Docker, Collector, and Compose files do not exist yet.

- [ ] **Step 3: Add the Ruby container and credential template**

Create a multi-stage or slim `Dockerfile` pinned to Ruby 3.3+ that installs from `Gemfile.lock`, copies only the app, runs as a non-root user, exposes `9292`, and starts Puma via `bundle exec rackup --host 0.0.0.0 --port 9292`. Add exactly the two variables from the design to `apps/ruby-otel/.env.example`.

- [ ] **Step 4: Add the dedicated Collector configuration**

Create the three-pipeline config described by the spec. Build a tiny Collector wrapper image from a concrete pinned Contrib image: copy its binary into a pinned Alpine runtime with CA certificates and the `bin/start-collector` entry point. The entry point validates nonblank `LOGFIRE_TOKEN` and `LOGFIRE_BASE_URL`, prints a fixed actionable error to stderr on failure, and otherwise `exec`s the Collector. Add a health-check extension and use Alpine's local HTTP utility for the Compose health check. Do not add `docker_stats`, host mounts, a public OTLP port, or any unrelated receiver.

- [ ] **Step 5: Wire the Compose profile and root port setting**

Add both services to `docker-compose.yml`. Pass only standard OTel settings to the app, including `OTEL_METRIC_EXPORT_INTERVAL=5000`, and pass the app env file only to the Collector. Make the app wait for the Collector's supported readiness/start condition without adding a third service. Add `RUBY_OTEL_PORT=8006` to root `.env.example`.

- [ ] **Step 6: Run static configuration tests**

Run: `cd apps/ruby-otel && bundle exec ruby -Itest test/config_test.rb`

Expected: all tests pass.

- [ ] **Step 7: Validate the Collector and rendered Compose configuration**

Create `apps/ruby-otel/.env` from its example with a dummy non-secret token only if it does not already exist; never overwrite an existing file. First run the wrapper image with an empty token and assert it exits nonzero with the documented message. Then run:

```bash
docker run --rm \
  -e LOGFIRE_TOKEN=dummy \
  -e LOGFIRE_BASE_URL=https://logfire-us.pydantic.dev \
  -v "$PWD/infra/otel-collector/ruby-otel.yaml:/etc/otelcol-contrib/config.yaml:ro" \
  <PINNED_COLLECTOR_IMAGE> validate --config=/etc/otelcol-contrib/config.yaml
docker compose --profile ruby-otel config
```

Expected: Collector validation exits `0`; rendered Compose includes both Ruby demo services and shows no Logfire credential in the Ruby service environment.

- [ ] **Step 8: Build the Ruby service image**

Run: `docker compose --profile ruby-otel build ruby-otel`

Expected: image builds successfully from the locked bundle.

- [ ] **Step 9: Commit container and Collector wiring**

```bash
git add apps/ruby-otel/Dockerfile apps/ruby-otel/Collector.Dockerfile apps/ruby-otel/bin/start-collector apps/ruby-otel/.env.example apps/ruby-otel/test/config_test.rb infra/otel-collector/ruby-otel.yaml docker-compose.yml .env.example .gitignore
git commit -m "feat: route ruby telemetry through otel collector"
```

### Task 4: Presenter Guide and End-to-End Local Verification

**Files:**
- Create: `apps/ruby-otel/README.md`
- Modify: `README.md`
- Modify: `apps/ruby-otel/test/otel_export_test.rb` only if verification exposes a real gap.

**Interfaces:**
- Consumes: the Compose profile, routes, service metadata, OTLP signal integration, and credential boundary from Tasks 1–3.
- Produces: a copy/paste presenter walkthrough and repository-level discovery for the demo.

- [ ] **Step 1: Write the presenter README**

Document prerequisites, `.env` setup, US/EU `LOGFIRE_BASE_URL`, start/stop commands, the exact `curl` calls for `/work` and `/error`, the five-second metric wait, and Logfire queries/filters for `service.name = 'ruby-otel-demo'`, operation `ruby-otel.work`, errors, logs, and metric `ruby_otel.requests`. Include a short architecture diagram showing Ruby → Collector → Logfire and state explicitly that the Ruby container has no Logfire credential.

- [ ] **Step 2: Document maturity and verification boundaries**

Link the official OpenTelemetry Ruby status and Logfire alternative-client guidance. State that trace support is stable while metrics/logs are development-status. Distinguish local proof (tests, Collector validation, Compose rendering) from confirmed remote arrival, which requires checking the exact Logfire project during setup.

- [ ] **Step 3: Add the demo to the repository README**

Add `ruby-otel` to the demo list with its default URL `http://localhost:8006` and note that it demonstrates language-neutral OTLP routing rather than Pydantic AI.

- [ ] **Step 4: Run the complete offline verification suite**

Run:

```bash
cd apps/ruby-otel
bundle check
bundle exec ruby -Itest test/app_test.rb
bundle exec ruby -Itest test/telemetry_test.rb
bundle exec ruby -Itest test/otel_export_test.rb
bundle exec ruby -Itest test/config_test.rb
bundle exec ruby -c app.rb
bundle exec ruby -c config.ru
bundle exec ruby -c lib/telemetry.rb
```

Expected: dependency check, every test, and every syntax check pass.

- [ ] **Step 5: Run final container/configuration verification**

Repeat Collector `validate`, `docker compose --profile ruby-otel config`, and `docker compose --profile ruby-otel build ruby-otel`. If a real `LOGFIRE_TOKEN` is already configured for this demo, start the profile, call `/work` and `/error`, then query or inspect the exact project for fresh `ruby-otel-demo` traces, logs, and metrics. If no real token is available, do not claim remote arrival and stop after the offline/loopback checks.

- [ ] **Step 6: Review the credential boundary**

Run searches over tracked files and the rendered Ruby service environment to confirm no real token is committed and no `LOGFIRE_*` variable reaches `ruby-otel`. Verify `git status --short` contains no `apps/ruby-otel/.env` entry.

- [ ] **Step 7: Commit documentation**

```bash
git add apps/ruby-otel/README.md README.md
git commit -m "docs: add ruby otel collector demo guide"
```

- [ ] **Step 8: Request final code review and address findings**

Use `superpowers:requesting-code-review`, then rerun the affected verification commands after any correction. Finish with `superpowers:verification-before-completion` before reporting success.
