# Ruby OpenTelemetry Collector Demo Design

## Intent

Create a compact customer-facing demo that proves a Ruby application can send standard
OpenTelemetry data to a local OpenTelemetry Collector, which then forwards that data to
Pydantic Logfire. The visible boundary is important: the Ruby application knows only the
Collector's internal Docker address and holds no Logfire credential; only the Collector
knows the Logfire endpoint and token.

The demo is for a live walkthrough, not production deployment. It should be quick to
start, easy to explain, and easy to verify from both the terminal and Logfire.

## Goals

- Run a small Sinatra service and a dedicated OpenTelemetry Collector with one Docker
  Compose profile.
- Route traces, logs, and metrics from Ruby to the Collector over OTLP/HTTP, then from
  the Collector to Logfire over OTLP/HTTP.
- Generate recognizable telemetry from normal, nested-work, and failing requests.
- Preserve useful service metadata and trace/log correlation where supported.
- Give the presenter a short, deterministic script for starting the demo, triggering
  each signal, and finding it in Logfire.
- Keep credentials out of the Ruby container and out of version control.
- Test application behavior without sending real telemetry or requiring Logfire.

## Non-goals

- This is not a Pydantic AI agent demo and does not use `demo_core`, a Gateway model,
  Pydantic Evals, or the Python `uv` workspace.
- It does not monitor Docker or host infrastructure. The Collector receives application
  telemetry only.
- It does not provide production hardening such as TLS between the app and Collector,
  tail sampling, queues, persistent buffering, autoscaling, or authentication on the
  Collector's local receiver.
- It does not attempt to represent a full Rails application.
- It does not claim that Ruby's metrics and logs SDKs are stable; upstream currently
  marks traces stable and metrics/logs as development.

## Repository Shape

Add `apps/ruby-otel/` as an intentional non-Python exception:

- `Gemfile` and committed `Gemfile.lock`
- `Dockerfile`
- `.env.example`
- `README.md`
- `config.ru`
- `lib/telemetry.rb`
- `app.rb`
- `test/` using Minitest and Rack::Test

Add `infra/otel-collector/ruby-otel.yaml` for the dedicated Collector configuration.
Wire both containers into `docker-compose.yml` under profiles `ruby-otel` and `all`, and
add `RUBY_OTEL_PORT=8006` to the root `.env.example`.

Unlike the standard `add-demo` Python template, this app has no `pyproject.toml`, source
package, Pydantic AI agent, or eval dataset. That deviation is the point of the demo and
will be documented in its README.

## Runtime Design

### Ruby service

Use a supported Ruby 3.3+ image, Sinatra, Puma, and the official OpenTelemetry Ruby gems.
Initialize OpenTelemetry before loading the Sinatra application or Puma. Configure the
SDK from standard environment variables rather than embedding a vendor endpoint:

- `OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector-ruby-otel:4318`
- `OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf`
- `OTEL_SERVICE_NAME=ruby-otel-demo`
- resource attributes for `service.version=0.1.0` and
  `deployment.environment.name=local`
- a short metric export interval suitable for a live demo

The Ruby container must not receive `LOGFIRE_TOKEN` or the Logfire base URL.

Use Sinatra/Rack instrumentation for inbound request spans. Add a manual child span to
the work route so the trace hierarchy is obvious. Use the Ruby OTel APIs to emit a
structured application log and increment a request counter with low-cardinality route
and outcome attributes. On shutdown, flush providers so the live demo does not lose the
last batch.

Ruby metrics and logs are development-status upstream, so pin compatible gem versions
in `Gemfile.lock`, keep their setup isolated in `lib/telemetry.rb`, and state the maturity
caveat in the README. If direct log/metric APIs differ in the resolved compatible gem
set, prefer the official current APIs rather than custom Collector-side fabrication.

### Routes

- `GET /health` returns a simple readiness response and is excluded from the presenter
  workflow.
- `GET /` returns JSON describing the demo and available routes.
- `GET /work` creates a normal server span, a named child span, one structured log, and
  a successful counter measurement, then returns JSON containing a stable operation
  label the presenter can search for.
- `GET /error` records a deliberate exception on the request span, emits an error log,
  increments the counter with an error outcome, and returns a sanitized JSON `500`.

Do not include secrets, request bodies, or high-cardinality values in telemetry.

### Collector

The dedicated Collector uses the Contrib image pinned to a concrete version. It exposes
OTLP/HTTP on the Compose network and has three pipelines—traces, metrics, and logs—with:

- `otlp` receiver using the HTTP endpoint on `0.0.0.0:4318`
- `memory_limiter` and `batch` processors
- `otlphttp/logfire` exporter whose base endpoint comes from
  `LOGFIRE_BASE_URL` and whose `Authorization` header comes from `LOGFIRE_TOKEN`
- a health-check extension used by Compose

Only the Collector loads `apps/ruby-otel/.env`. The app receives its OTel settings from
the Compose service block. The Collector is not published to the host by default; the
Ruby container reaches it by service name.

The app depends on the Collector health check. Both services belong to the `ruby-otel`
and `all` profiles. The app is published as `${RUBY_OTEL_PORT:-8006}:9292`.

## Configuration and Credential Boundary

`apps/ruby-otel/.env.example` contains:

```dotenv
LOGFIRE_TOKEN=
LOGFIRE_BASE_URL=https://logfire-us.pydantic.dev
```

The README explains changing the base URL for an EU project. A real `.env` remains
gitignored. There is no `PYDANTIC_AI_GATEWAY_API_KEY` because the service never calls a
model. Compose passes this env file only to the Collector, demonstrating that the Ruby
application is backend-agnostic and credential-free.

## Presenter Experience

The README gives a concise flow:

1. Copy `.env.example` to `.env` and supply a Logfire write token.
2. Start `docker compose --profile ruby-otel up --build`.
3. Call `/work` and `/error` with `curl`.
4. Wait for the short metric export interval.
5. In Logfire, filter for `service.name = 'ruby-otel-demo'`, inspect the nested work
   trace and correlated log, inspect the recorded exception, and find the request
   counter in Metrics.
6. Show the Compose/configuration boundary: Ruby points at the Collector service, while
   only the Collector has the Logfire destination and token.

The README must distinguish local verification from remote verification: repository
tests and Collector logs prove configuration/startup, but successful arrival in Logfire
must be confirmed in the actual target project during the demo setup.

## Tests and Verification

Application tests run with the OpenTelemetry SDK/exporters disabled and cover all routes,
response schemas, and the sanitized error behavior. They must not require a write token,
network, Docker, or real telemetry export.

Implementation verification includes:

- Ruby tests.
- Ruby syntax checks.
- Bundler dependency validation.
- Collector configuration validation using the pinned Collector image.
- `docker compose --profile ruby-otel config` with a temporary local `.env` containing a
  dummy token.
- A local Compose smoke test that starts both services with a non-production/debug
  exporter option or an equivalent isolated check, calls `/work` and `/error`, and
  confirms the app successfully exports all three OTLP signal types to the Collector
  without attempting to assert arrival in Logfire.

No completion claim will say data reached Logfire unless that exact service and fresh
operations were queried or viewed in the configured project during the same session.

## Builder Decisions

The implementer may choose the exact supported Ruby 3.3+ patch version, compatible gem
versions, minimal JSON serializer, and test organization. They may adjust the Collector
health-check mechanism to what the pinned image supports. These choices must preserve the
signal flow, credential boundary, routes, and presenter experience above.
