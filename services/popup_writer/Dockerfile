# syntax=docker/dockerfile:1

# -----------------------------------------------------------------------------
# Stage 1: Build virtual environment with uv BuildKit cache
# -----------------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# 1. Install external dependencies only (cached across source changes).
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=shared/pyproject.toml,target=shared/pyproject.toml \
    --mount=type=bind,source=services/server/pyproject.toml,target=services/server/pyproject.toml \
    --mount=type=bind,source=services/popup_writer/pyproject.toml,target=services/popup_writer/pyproject.toml \
    --mount=type=bind,source=services/streamer/pyproject.toml,target=services/streamer/pyproject.toml \
    --mount=type=bind,source=services/agent/pyproject.toml,target=services/agent/pyproject.toml \
    uv sync --frozen --no-dev --no-install-workspace --package cp-popup-writer

# 2. Copy source and build the workspace packages into the environment.
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package cp-popup-writer

# -----------------------------------------------------------------------------
# Stage 2: Minimal & Secure Runtime
# -----------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN groupadd -r appgroup && useradd -r -g appgroup -u 10001 appuser

COPY --from=builder --chown=appuser:appgroup /app/.venv /app/.venv

USER appuser

ENTRYPOINT ["python", "-m", "popup_writer.main"]
