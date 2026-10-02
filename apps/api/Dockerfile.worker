# syntax=docker/dockerfile:1@sha256:ecfaec9ed6d810b56388c508f4121597bfbba70d41a6dfeee4d8cad5f295fc32
# ─────────────────────────────────────────────────────────────
# SourceMind API — Multi-stage Dockerfile
# Targets: base → development | production
#
# Multi-arch: buildx supports linux/amd64 + linux/arm64
# ─────────────────────────────────────────────────────────────

# ── Stage 1: Pinned build tooling ────────────────────────────
FROM ghcr.io/astral-sh/uv:0.10.9@sha256:10902f58a1606787602f303954cea099626a4adb02acbac4c69920fe9d278f82 AS uv

# ── Stage 2: Python base ─────────────────────────────────────
FROM python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS base

COPY --from=uv /uv /uvx /usr/local/bin/

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:${PATH}" \
    UV_CONCURRENT_DOWNLOADS=8 \
    UV_HTTP_RETRIES=6

# BuildKit cache mounts speed repeated builds without entering image layers.
# `uv sync --locked` still verifies every artifact against the committed lock.

# Security: run as non-root
RUN groupadd --gid 1001 sourcemind \
    && useradd --uid 1001 --gid sourcemind --shell /bin/bash --create-home sourcemind

# System deps required by Python packages. Repository snapshots freeze package
# resolution while remaining portable across amd64 and arm64 builds.
RUN printf '%s\n' \
        'Types: deb' \
        'URIs: https://snapshot.debian.org/archive/debian/20260928T000000Z' \
        'Suites: bookworm bookworm-updates' \
        'Components: main' \
        'Check-Valid-Until: no' \
        '' \
        'Types: deb' \
        'URIs: https://snapshot.debian.org/archive/debian-security/20260928T000000Z' \
        'Suites: bookworm-security' \
        'Components: main' \
        'Check-Valid-Until: no' \
        > /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
    # asyncpg / psycopg build deps
    libpq-dev \
    # PyMuPDF (PDF processing)
    libmupdf-dev \
    # tree-sitter
    gcc \
    g++ \
    # curl for health checks
    curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Stage 3: Dependencies ─────────────────────────────────────
FROM base AS deps

# hatchling's wheel target is packages = ["sourcemind"], so source must exist
# before `uv sync` builds and installs the project wheel. Compose bind-mounts
# the working tree over /app at development runtime.
COPY pyproject.toml uv.lock ./
COPY sourcemind/ ./sourcemind/

# Install the exact locked dependency graph, including development extras.
RUN --mount=type=cache,id=s/45ede657-a5f2-41c2-8319-d084731a4fc6-/root/.cache/uv,target=/root/.cache/uv,sharing=locked \
    uv sync --locked --extra dev --no-editable

# ── Stage 4: Development ──────────────────────────────────────
FROM deps AS development

# Development: mount source code as volume for hot reload
# (docker-compose.yml mounts /app from the host)
USER sourcemind

ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV ENVIRONMENT=development

# Expose the API port
EXPOSE 8000

# Default command (overridden by docker-compose for hot reload)
CMD ["uvicorn", "sourcemind.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ── Stage 5: Production build ─────────────────────────────────
FROM base AS production

# ORDER IS LOAD-BEARING — do not move the install above these COPYs.
#
# hatchling's wheel target is packages = ["sourcemind"]. The source directory
# must be present when `uv sync --no-editable` builds the project wheel;
# otherwise the backend can emit an empty but apparently valid wheel.
#
# Guarded by scripts/check_wheel.py, which asserts the installed package
# contains real modules. Run it in CI after building.
COPY pyproject.toml uv.lock ./
COPY sourcemind/ ./sourcemind/

# Install the exact locked production dependency graph.
RUN --mount=type=cache,id=s/45ede657-a5f2-41c2-8319-d084731a4fc6-/root/.cache/uv,target=/root/.cache/uv,sharing=locked \
    uv sync --locked --no-dev --no-editable

# Runtime-only files — not part of the wheel, so they can land after install.
COPY alembic/ ./alembic/
COPY alembic.ini ./

# Security: own files as root, run as non-root
RUN chown -R sourcemind:sourcemind /app
USER sourcemind

ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV ENVIRONMENT=production

# Deliberately no EXPOSE. The server binds whatever $PORT the platform
# injects, so a hardcoded value here is not merely redundant — it is a
# wrong hint. Platforms use EXPOSE to infer the target port, and an
# EXPOSE 8000 against an app listening on an injected 8080 routed the
# edge to a dead port: the healthcheck passed internally while every
# public request returned 502.

# Production runtime only. Schema migrations require the migration-owner
# credential and run as a separate one-off release command before API and
# worker rollout; the API runtime credential must not own or alter schema.
# NOTE: the ["sh", "-c", ...] form is required, not cosmetic. ${PORT:-8000}
# is shell parameter expansion; with exec-form CMD (no shell) uvicorn would
# receive the literal string and exit with
#   Error: Invalid value for '--port': '${PORT:-8000}' is not a valid integer.
CMD ["sh", "-c", "exec uvicorn sourcemind.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --no-access-log"]

# Health check for Docker / ECS
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

LABEL org.opencontainers.image.title="SourceMind API"
LABEL org.opencontainers.image.description="Collaborative team memory platform"
LABEL org.opencontainers.image.vendor="SourceMind"

# ── Stage 6: Build-time validation — CI only, never deployed ────────────
FROM production AS validated

USER root
COPY --chown=sourcemind:sourcemind scripts/ ./scripts/
USER sourcemind

RUN ENVIRONMENT=development AUTH_DEV_BYPASS_ENABLED=true \
    python scripts/check_wheel.py \
    && python -c "import torch; from sentence_transformers import SentenceTransformer; assert torch.__version__ == '2.14.0+cpu'; assert torch.version.cuda is None; assert not torch.cuda.is_available(); print('PASS: CPU-only Torch and SentenceTransformer import')"
