FROM python:3.11-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/demo_core ./packages/demo_core
COPY apps/chat ./apps/chat

RUN uv sync --frozen --package chat

EXPOSE 8000
CMD ["uv", "run", "--package", "chat", "uvicorn", "chat.main:app", "--host", "0.0.0.0", "--port", "8000"]
