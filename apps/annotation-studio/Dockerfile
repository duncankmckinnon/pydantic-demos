FROM node:20-slim AS frontend-build
WORKDIR /app/apps/annotation-studio/frontend
COPY apps/annotation-studio/frontend/package.json apps/annotation-studio/frontend/package-lock.json ./
RUN npm ci
COPY apps/annotation-studio/frontend ./
RUN npm run build

FROM python:3.11-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/demo_core ./packages/demo_core
COPY apps/annotation-studio ./apps/annotation-studio
COPY --from=frontend-build /app/apps/annotation-studio/src/annotation_studio/static/dist \
     ./apps/annotation-studio/src/annotation_studio/static/dist

RUN uv sync --frozen --package annotation-studio

EXPOSE 8000
CMD ["uv", "run", "--package", "annotation-studio", "uvicorn", \
     "annotation_studio.main:app", "--host", "0.0.0.0", "--port", "8000"]
