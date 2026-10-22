# syntax=docker/dockerfile:1.7
# FastAPI service. Build from the REPOSITORY ROOT (it needs the uv workspace + lock file):
#   docker build -f services/api/Dockerfile -t export-api .

FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.18 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src

# 1) Third-party dependencies only: this layer is cached until uv.lock changes.
COPY pyproject.toml uv.lock ./
COPY services/shared/pyproject.toml services/shared/
COPY services/api/pyproject.toml services/api/
COPY services/worker/pyproject.toml services/worker/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package export-api --no-install-workspace

# 2) Our code, installed non-editable so the runtime image needs no source tree.
COPY services/shared services/shared
COPY services/api services/api
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package export-api --no-editable

FROM python:3.12-slim
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER 10001
EXPOSE 8000
# /api/health also checks the dependencies (DB, S3, SQS, DynamoDB): "healthy" means "ready".
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status != 200)"]
CMD ["uvicorn", "export_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-server-header"]
