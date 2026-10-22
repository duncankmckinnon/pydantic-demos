# Ruby OpenTelemetry Collector Demo

A small Sinatra service that sends traces, logs, and metrics to a local OpenTelemetry
Collector over OTLP/HTTP. The Collector forwards those signals to Logfire.

```text
┌──────────────────────────┐       OTLP/HTTP       ┌────────────────────────┐
│ Ruby / Sinatra           │ ────────────────────> │ OpenTelemetry Collector│
│ ruby-otel-demo           │                       │ owns Logfire credential│
└──────────────────────────┘                       └────────────┬───────────┘
                                                              │ OTLP/HTTP
                                                              ▼
                                                    ┌───────────────────────┐
                                                    │ Pydantic Logfire      │
                                                    └───────────────────────┘
```

The Ruby container receives only the Collector's internal address. It never receives a
Logfire token or Logfire endpoint, which makes the application instrumentation portable
to another OpenTelemetry backend.

## Prerequisites

- Docker with Docker Compose
- A Logfire project write token

## Configure

From the repository root:

```bash
cp apps/ruby-otel/.env.example apps/ruby-otel/.env
```

Set `LOGFIRE_TOKEN` in that file. US projects use the default endpoint:

```dotenv
LOGFIRE_TOKEN=your-write-token
LOGFIRE_BASE_URL=https://logfire-us.pydantic.dev
```

For an EU project, change the endpoint to `https://logfire-eu.pydantic.dev`. The `.env`
file is gitignored and is loaded only by `otel-collector-ruby-otel`.

## Run the demo

Start the Ruby service and its Collector:

```bash
docker compose --profile ruby-otel up --build
```

The app is available at <http://localhost:8006>. Override the host port by setting
`RUBY_OTEL_PORT` in the repository root `.env`.

In another terminal, trigger a successful trace and a recorded failure:

```bash
curl --fail-with-body http://localhost:8006/work
curl --fail-with-body http://localhost:8006/error
```

The second command intentionally returns HTTP `500`, so `curl` exits nonzero after
printing the sanitized response. Wait at least five seconds for the configured metric
export interval.

Then show these results in the configured Logfire project:

1. In Live, filter to `service_name = 'ruby-otel-demo'`.
2. Open the `ruby-otel.work` trace and show the nested application span and correlated
   `ruby work completed` log.
3. Open the `/error` request and show its error status, standard exception event, and
   `ruby work failed` log.
4. In Metrics, select `ruby_otel.requests` and group by `route` and `outcome`.

To make the routing boundary visible, render the profile and compare the environments:

```bash
docker compose --profile ruby-otel config
```

`LOGFIRE_TOKEN` and `LOGFIRE_BASE_URL` appear only under the Collector service. The Ruby
service has standard `OTEL_*` settings pointing to `otel-collector-ruby-otel:4318`.

Stop and remove the demo containers with:

```bash
docker compose --profile ruby-otel down
```

## What the routes emit

- `GET /work` creates the HTTP server span, a `ruby-otel.work` child span, a structured
  info log, and a `ruby_otel.requests` counter measurement with bounded attributes.
- `GET /error` records an exception and error status, emits a structured error log, and
  increments the same counter with `outcome=error`. Its HTTP response contains no
  backtrace or internal exception message.
- `GET /health` is a readiness endpoint.

Service metadata is fixed to `service.name=ruby-otel-demo`, `service.version=0.1.0`, and
`deployment.environment.name=local`.

## Verification boundary

The repository tests verify route behavior, credential isolation, provider shutdown,
Collector configuration, and actual protobuf POSTs for traces, metrics, and logs against
a loopback OTLP receiver. Collector validation and Docker builds verify the deployable
configuration locally.

Those checks do not prove data arrived in a particular Logfire project. Before the live
demo, use that project's real write token, trigger fresh requests, and confirm the exact
`ruby-otel-demo` records in that project.

OpenTelemetry currently marks Ruby tracing as stable and Ruby metrics and logs as
development-status. Their compatible gem versions are therefore locked for this demo;
review the lockfile when upgrading. See the official
[OpenTelemetry Ruby status](https://opentelemetry.io/docs/languages/ruby/) and Logfire's
[alternative-client guidance](https://pydantic.dev/docs/logfire/guides/alternative-clients/).

This demo intentionally does not use Pydantic AI, `demo_core`, the Gateway, or Pydantic
Evals. Its purpose is to show that Logfire accepts standard OpenTelemetry from Ruby
through a Collector.
