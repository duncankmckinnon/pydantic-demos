FROM python:3.11-slim

RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/demo_core ./packages/demo_core
COPY apps/audio-demo ./apps/audio-demo
RUN uv sync --frozen --package audio-demo

EXPOSE 8000
CMD ["uv", "run", "--package", "audio-demo", "uvicorn", "audio_demo.main:app", "--host", "0.0.0.0", "--port", "8000"]
