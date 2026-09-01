# Chat

A general-purpose chat assistant demo built with Pydantic AI: web search, per-conversation
memory, and live Pydantic AI docs lookup, running on Claude or GPT via the Gateway (see
`MODEL_CHOICES` in `src/chat/agent.py`).

## Configure

```bash
cd apps/chat
cp .env.example .env   # fill in PYDANTIC_AI_GATEWAY_API_KEY, LOGFIRE_TOKEN
```

## Run on the host

```bash
uv run --package chat uvicorn chat.main:app --reload
```

Open http://localhost:8000.

## Run in Docker

From the repo root:

```bash
docker compose --profile chat up --build -d
```

Open http://localhost:8001 by default — override with `CHAT_PORT` in a `.env` at the repo
root (copy the root's `.env.example`) if you want a different host port.
