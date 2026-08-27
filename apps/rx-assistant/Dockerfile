FROM python:3.11-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/demo_core ./packages/demo_core
COPY apps/rx-assistant ./apps/rx-assistant

RUN uv sync --frozen --package rx-assistant

EXPOSE 8000
CMD ["uv", "run", "--package", "rx-assistant", "uvicorn", "rx_assistant.main:app", "--host", "0.0.0.0", "--port", "8000"]
