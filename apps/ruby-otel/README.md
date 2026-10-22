# Ruby OpenTelemetry Collector Demo

A Sinatra travel agent that uses live weather and hotel data plus a real model call through
Pydantic AI Gateway. It sends AI-style traces, logs, and metrics to a local OpenTelemetry
Collector over OTLP/HTTP, and the Collector forwards those signals to Logfire.

```text
                                      ┌──────────────────────────┐
                                 ┌──> │ Pydantic AI Gateway      │
                                 ├──> │ Open-Meteo               │
┌──────────────────────────┐     └──> │ OpenStreetMap / Nominatim│
│ Ruby travel agent        │           └──────────────────────────┘
│ ruby-otel-demo           │
└────────────┬─────────────┘       OTLP/HTTP       ┌────────────────────────┐
             └───────────────────────────────────> │ OpenTelemetry Collector│
                                                   │ owns Logfire credential│
                                                   └────────────┬───────────┘
                                                                │ OTLP/HTTP
                                                                ▼
                                                      ┌─────────────────────┐
                                                      │ Pydantic Logfire    │
                                                      └─────────────────────┘
```

The Ruby container receives the Gateway key and the Collector's internal address. It never
receives a Logfire token or Logfire endpoint, which keeps the application instrumentation
portable to another OpenTelemetry backend.

## Prerequisites

- Docker with Docker Compose
- A Logfire project write token
- A Pydantic AI Gateway API key with an OpenAI-compatible route
- Internet access to Pydantic AI Gateway, Open-Meteo, and OpenStreetMap Nominatim

## Configure

From the repository root:

```bash
cp apps/ruby-otel/.env.example apps/ruby-otel/.env
cp apps/ruby-otel/agent.env.example apps/ruby-otel/agent.env
```

Set the Collector's `LOGFIRE_TOKEN` in `.env`. US projects use the default endpoint:

```dotenv
LOGFIRE_TOKEN=your-write-token
LOGFIRE_BASE_URL=https://logfire-us.pydantic.dev
```

For an EU project, change the endpoint to `https://logfire-eu.pydantic.dev`. The `.env`
file is gitignored and is loaded only by `otel-collector-ruby-otel`.

Set the Gateway key in `agent.env`:

```dotenv
PYDANTIC_AI_GATEWAY_API_KEY=your-gateway-key
PYDANTIC_AI_GATEWAY_BASE_URL=https://gateway-us.pydantic.dev/proxy/chat/
PYDANTIC_AI_GATEWAY_MODEL=gpt-5.6
```

For an EU Gateway route, use `https://gateway-eu.pydantic.dev/proxy/chat/`. This second
gitignored file is loaded only by the Ruby service and contains no Logfire credential.

## Run the demo

Start the Ruby service and its Collector:

```bash
docker compose --profile ruby-otel up --build
```

The app is available at <http://localhost:8006>. Override the host port by setting
`RUBY_OTEL_PORT` in the repository root `.env`.

In another terminal, run the live travel agent and optionally trigger the original telemetry
examples:

```bash
curl --fail-with-body \
  --header 'Content-Type: application/json' \
  --data '{"destination":"Kyoto, Japan"}' \
  http://localhost:8006/travel

curl --fail-with-body http://localhost:8006/work
curl --fail-with-body http://localhost:8006/error
```

The second command intentionally returns HTTP `500`, so `curl` exits nonzero after
printing the sanitized response. Wait at least five seconds for the configured metric
export interval.

Then show these results in the configured Logfire project:

1. In Live, filter to `service_name = 'ruby-otel-demo'`.
2. Open `invoke_agent travel-planner` and show its two `chat <model>` spans plus
   `execute_tool get_weather` and `execute_tool find_hotels`.
3. Show `gen_ai.request.model`, response model and ID, token usage, tool-call IDs, and the
   live tool results on their respective spans.
4. Show that both model calls went through Gateway while all telemetry went Ruby → Collector
   → Logfire.
5. In Metrics, select `ruby_otel.requests` and group by `route` and `outcome`.

To make the routing boundary visible, render the profile and compare the environments:

```bash
docker compose --profile ruby-otel config
```

`LOGFIRE_TOKEN` and `LOGFIRE_BASE_URL` appear only under the Collector service. The Ruby
service gets `PYDANTIC_AI_GATEWAY_*` from `agent.env` plus standard `OTEL_*` settings
pointing to `otel-collector-ruby-otel:4318`.

Stop and remove the demo containers with:

```bash
docker compose --profile ruby-otel down
```

## What the routes emit

- `POST /travel` accepts `{"destination":"Kyoto, Japan"}`. Its HTTP server span contains
  an `invoke_agent travel-planner` span, two model spans, and live weather/hotel tool spans.
  It increments `ruby_otel.requests` with `route=/travel`. External failures return a
  sanitized `502` and are recorded on the trace.
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

Weather comes from [Open-Meteo](https://open-meteo.com/). Hotel listings come from
[OpenStreetMap](https://www.openstreetmap.org/copyright) through a bounded public Nominatim
search. They are discovery results, not verified prices, availability, ratings, or bookings.
Nominatim use is limited to one request per second and requires attribution; this demo enforces
that request interval and is intended only for low-volume, directly triggered local demos. See
the [Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/), and make
one rehearsal request shortly before the demo.

OpenTelemetry currently marks Ruby tracing as stable and Ruby metrics and logs as
development-status. Their compatible gem versions are therefore locked for this demo;
review the lockfile when upgrading. See the official
[OpenTelemetry Ruby status](https://opentelemetry.io/docs/languages/ruby/) and Logfire's
[alternative-client guidance](https://pydantic.dev/docs/logfire/guides/alternative-clients/).

This demo uses the Gateway's OpenAI-compatible HTTP API directly rather than Pydantic AI,
because Pydantic AI is a Python framework and the customer-facing application is Ruby. Its
purpose is to show a real model-and-tools agent trace emitted with standard OpenTelemetry
from Ruby and routed through a Collector.
