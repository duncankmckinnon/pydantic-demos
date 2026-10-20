FROM python:3.11-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/demo_core ./packages/demo_core
COPY apps/applicant-support ./apps/applicant-support

RUN uv sync --frozen --package applicant-support

EXPOSE 8000
CMD ["uv", "run", "--package", "applicant-support", "uvicorn", "applicant_support.main:app", "--host", "0.0.0.0", "--port", "8000"]
