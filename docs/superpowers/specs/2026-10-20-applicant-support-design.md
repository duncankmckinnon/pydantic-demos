# Applicant Support Demo Design

## Intent

Build a small, local-only HR customer-service chatbot for a job applicant portal. This
first version is an intentionally weak baseline: it should have enough data and tools to
answer realistic applicant questions, but only a short generic instruction so later work
can demonstrate how evals expose and guide agent improvements.

## Goals

- Answer questions about an applicant's screening status and interview schedule.
- Reschedule an existing applicant interview in the demo database.
- Answer whether another role is currently open.
- Ship sample applicants, applications, interviews, and job openings automatically.
- Include a browser chat interface and a JSON chat endpoint.
- Include a small offline `pydantic_evals` dataset covering the core scenarios.
- Keep unit tests fully offline by overriding the agent with Pydantic AI test models.

## Non-goals

- Production authentication, authorization, audit logging, or applicant identity proofing.
- Real recruiting-system integrations, email/calendar changes, or durable multi-user scale.
- A high-quality production system prompt, policy layer, or sophisticated evaluation suite.
- Online evals, harness capabilities, or infrastructure monitoring in this first increment.

## Design

The app will be named `applicant-support` as a package and Compose profile, with Python
imports under `applicant_support`. It will use SQLite from the Python standard library,
stored at `apps/applicant-support/data/applicants.sqlite3` by default and seeded
idempotently at startup. The seed data will include multiple applicants and applications,
at least one scheduled screening call, and both open and closed roles.

The UI will ask for an application ID alongside the chat message. The application ID is
passed as an agent dependency rather than trusted from model text. The agent will expose
three tools: look up the current application, reschedule its call, and search roles by
title. Its deliberately basic instruction will only say that it is an HR representative,
should answer applicant questions, and may use its tools.

FastAPI will expose `GET /` and `POST /api/chat`. Conversation history will remain an
in-memory, local-demo-only session store, while applicant and role changes persist in
SQLite. Logfire is configured before constructing the agent or app, and model calls use
`demo_core.models.get_model` with per-app Gateway credentials.

The initial offline eval dataset will include status lookup, call rescheduling, open-role
lookup, a closed-role question, an unknown application, and a question that should not
invent unavailable applicant data. Expected outputs will be lightweight scenario metadata
for future evaluator expansion; the first runner will execute the real agent manually and
record traces in Logfire.

## Constraints and deliberate limitations

- Everything runs locally and follows the repository's per-app `.env` convention.
- The default test suite never calls a real model or sends telemetry.
- SQLite access remains synchronous because the demo data and request volume are tiny.
- Rescheduling accepts an ISO 8601 timestamp and does not integrate with a real calendar.
- The baseline agent is intentionally under-instructed; poor behavior is useful eval input.
- Port `8005` is the default host port, following the existing sequence in this repository.

## Builder calls

- Use application IDs such as `APP-1001` as the demo identity mechanism.
- Keep the frontend dependency-free and visually aligned with the existing `chat` demo.
- Do not add Pydantic AI Harness until a later increment actually needs it.
