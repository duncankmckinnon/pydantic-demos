# Ruby OpenTelemetry Collector Demo

A Sinatra travel agent that accepts a destination, checks live weather and nearby hotel
listings, and asks `gpt-5.6-terra` for a grounded recommendation through Pydantic AI
Gateway's OpenAI Responses API. It sends AI-style traces, logs, and metrics to a local
OpenTelemetry Collector over OTLP/HTTP. The Collector also gathers host and scoped Docker
metrics, then forwards every signal to Logfire.

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

This local demo opts into prompt and completion capture with
`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=span_only`. The agent and model spans
record `gen_ai.input.messages`, `gen_ai.output.messages`, and `gen_ai.system_instructions`.
Set that Compose environment value to `no_content` when prompts or responses may contain
sensitive data.

## Prerequisites

- Docker with Docker Compose
- Access to the local Docker socket (Docker Desktop or Docker Engine)
- A Logfire project write token
- A Pydantic AI Gateway API key with access to the `openai-responses` route and
  `gpt-5.6-terra`
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
PYDANTIC_AI_GATEWAY_BASE_URL=https://gateway-us.pydantic.dev/proxy/openai-responses
PYDANTIC_AI_GATEWAY_MODEL=gpt-5.6-terra
NOMINATIM_ENDPOINT=https://nominatim.openstreetmap.org/search
```

For the equivalent EU Gateway route, use
`https://gateway-eu.pydantic.dev/proxy/openai-responses`. This second
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

The `/error` command intentionally returns HTTP `500`, so `curl` exits nonzero after
printing the sanitized response. Wait at least five seconds for the configured metric
export interval.

Then show these results in the configured Logfire project:

1. In Live, filter to `service_name = 'ruby-otel-demo'`.
2. Open `invoke_agent travel-planner`. Its `gen_ai.input.messages` contains the destination
   request and its `gen_ai.output.messages` contains the final recommendation.
3. Expand its two `chat gpt-5.6-terra` spans and the `execute_tool get_weather` and
   `execute_tool find_hotels` spans. The model spans contain the detailed two-turn
   conversation, response IDs, token usage, tool-call IDs, and tool results.
4. Show that both model calls went through Gateway while all telemetry went Ruby → Collector
   → Logfire.
5. In Metrics, select `ruby_otel.requests` and group by `route` and `outcome`.
6. Open **Hosts** and select `ruby-otel-local-host` to show CPU, memory, disk, filesystem,
   load, network, and process telemetry.
7. Open **Docker** to show CPU, memory, network, and block I/O for the `ruby-otel` container.

Infrastructure metrics are collected every 30 seconds. The Docker receiver maps Compose
labels into resource attributes and drops every container whose `compose.service` is not
`ruby-otel`; unrelated local containers and the Collector itself are not exported. Both
pipelines reuse the same Logfire token as the Ruby demo, so they appear in that demo's
project without centralizing credentials.

On Docker Desktop, host metrics describe Docker's Linux virtual machine rather than the
macOS host itself. The Collector runs as root and mounts `/` read-only at `/hostfs` so the
hostmetrics receiver can inspect that environment. It also mounts `docker.sock` read-only,
but access to that socket is effectively root-level control of Docker; only run this local
demo with a trusted Collector image and configuration.

Validate the rendered profile without printing its secret-bearing environment:

```bash
docker compose --profile ruby-otel config --quiet
```

In `docker-compose.yml`, `LOGFIRE_TOKEN` and `LOGFIRE_BASE_URL` are loaded only for the
Collector service. The Ruby service gets `PYDANTIC_AI_GATEWAY_*` and `NOMINATIM_ENDPOINT`
from `agent.env` plus standard `OTEL_*` settings pointing to
`otel-collector-ruby-otel:4318`. Avoid an unfiltered `docker compose config` in a recorded
demo because it renders credential values.

Stop and remove the demo containers with:

```bash
docker compose --profile ruby-otel down
```

## What the routes emit

- `POST /travel` accepts `{"destination":"Kyoto, Japan"}`. Its HTTP server span contains
  an `invoke_agent travel-planner` span, two model spans, and live weather/hotel tool spans.
  The agent span records the top-level request and recommendation; the model spans record
  system instructions and the detailed Responses API conversation. It increments
  `ruby_otel.requests` with `route=/travel`. External failures return a sanitized `502` and
  are recorded on the trace. Destinations must be non-empty strings of at most 100
  characters.
- `GET /work` creates the HTTP server span, a `ruby-otel.work` child span, a structured
  info log, and a `ruby_otel.requests` counter measurement with bounded attributes.
- `GET /error` records an exception and error status, emits a structured error log, and
  increments the same counter with `outcome=error`. Its HTTP response contains no
  backtrace or internal exception message.
- `GET /health` is a readiness endpoint.

Service metadata is fixed to `service.name=ruby-otel-demo`, `service.version=0.1.0`, and
`deployment.environment.name=local`.

## Verification boundary

The repository tests verify route behavior, Responses API tool calls and continuation,
request/response content capture and opt-out, credential isolation, provider shutdown,
Collector configuration, and actual protobuf POSTs for traces, metrics, and logs against a
loopback OTLP receiver. Collector validation and Docker builds verify the deployable
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
one rehearsal request shortly before the demo. `NOMINATIM_ENDPOINT` makes the provider
switchable without a code change.

The destination and external tool inputs/results are attached to demo spans so the agent flow is
easy to inspect in Logfire. Do not submit personal, confidential, or regulated data to this demo.

OpenTelemetry currently marks Ruby tracing as stable and Ruby metrics and logs as
development-status. Their compatible gem versions are therefore locked for this demo;
review the lockfile when upgrading. See the official
[OpenTelemetry Ruby status](https://opentelemetry.io/docs/languages/ruby/) and Logfire's
[alternative-client guidance](https://pydantic.dev/docs/logfire/guides/alternative-clients/).

This demo uses the Gateway's OpenAI Responses HTTP API directly rather than Pydantic AI,
because Pydantic AI is a Python framework and the customer-facing application is Ruby. Its
purpose is to show a real model-and-tools agent trace emitted with standard OpenTelemetry
from Ruby and routed through a Collector.
