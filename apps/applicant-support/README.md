# Applicant Support

A deliberately basic HR customer-service agent for a job applicant portal. It uses
Pydantic AI tools to read and update a seeded SQLite database, giving the eval suite a
simple baseline that can be improved in later iterations.

## Sample data

| Application | Applicant | Role | Status |
| --- | --- | --- | --- |
| `APP-1001` | Maya Chen | Product Engineer | Screening passed; call scheduled |
| `APP-1002` | Jordan Lee | Product Designer | Screening pending |
| `APP-1003` | Sam Rivera | Customer Support Specialist | Not selected |

The Product Engineer and Customer Support Specialist roles are open. Product Designer is
closed.

## Configure

```bash
cd apps/applicant-support
cp .env.example .env
# Fill in PYDANTIC_AI_GATEWAY_API_KEY and LOGFIRE_TOKEN.
```

## Run locally

From the repository root:

```bash
uv run --package applicant-support uvicorn applicant_support.main:app --reload
```

Open http://localhost:8000 and use one of the sample application IDs above.

## Run with Docker

```bash
docker compose --profile applicant-support up --build
```

Open http://localhost:8005. Set `APPLICANT_SUPPORT_PORT` in the repository root `.env` to
change the host port.

## Run the baseline eval questions

```bash
uv run --package applicant-support python -m applicant_support.evals.run
```

This makes real model calls through the configured Gateway and records the instrumented
runs in Logfire. The initial dataset deliberately focuses on representative cases and
expected outcomes; richer evaluators are intended for the next iteration.
