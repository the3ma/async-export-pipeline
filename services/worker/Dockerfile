# syntax=docker/dockerfile:1.7
# Local export worker: long-polls SQS (LocalStack) and runs the same handler as the Lambda.
# Build from the REPOSITORY ROOT (it needs the uv workspace + lock file):
#   docker build -f services/worker/Dockerfile -t export-worker .

FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.18 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src

COPY pyproject.toml uv.lock ./
COPY services/shared/pyproject.toml services/shared/
COPY services/api/pyproject.toml services/api/
COPY services/worker/pyproject.toml services/worker/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package export-worker --no-install-workspace

COPY services/shared services/shared
COPY services/worker services/worker
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package export-worker --no-editable

FROM python:3.12-slim
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin worker \
    && mkdir -p /tmp/exports && chown worker /tmp/exports
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TMP_DIR=/tmp/exports
USER 10001
# SIGTERM lets the poller finish the message in hand before exiting.
STOPSIGNAL SIGTERM
CMD ["python", "-m", "export_worker.poller"]
