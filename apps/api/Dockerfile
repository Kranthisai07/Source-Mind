# ─────────────────────────────────────────────────────────────
# SourceMind API — Multi-stage Dockerfile
# Targets: base → development | production
#
# Multi-arch: buildx supports linux/amd64 + linux/arm64
# ─────────────────────────────────────────────────────────────

# ── Stage 1: Python base ─────────────────────────────────────
FROM python:3.12-slim-bookworm AS base

# Security: run as non-root
RUN groupadd --gid 1001 sourcemind \
    && useradd --uid 1001 --gid sourcemind --shell /bin/bash --create-home sourcemind

# System deps required by Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
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

# ── Stage 2: Dependencies ─────────────────────────────────────
FROM base AS deps

# hatchling's wheel target is packages = ["sourcemind"], so that directory
# must exist before `pip install -e` runs — the build backend has nothing to
# build otherwise. Source is bind-mounted over this at runtime by compose.
COPY pyproject.toml ./
COPY sourcemind/ ./sourcemind/

# Install all dependencies (including dev extras for the development stage)
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir hatchling \
    && pip install --no-cache-dir -e ".[dev]"

# ── Stage 3: Development ──────────────────────────────────────
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

# ── Stage 4: Production build ─────────────────────────────────
FROM base AS production

# Build inputs must land before the install. hatchling's wheel target is
# packages = ["sourcemind"]; if that directory is absent the build still
# SUCCEEDS but emits an empty wheel containing zero package files, which
# would silently break a non-editable `pip install "."`.
COPY pyproject.toml ./
COPY sourcemind/ ./sourcemind/

# Install production dependencies only (no dev extras)
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir hatchling \
    && pip install --no-cache-dir -e "."

# Chromium for URL ingestion (services/ingestion/extractor.py launches
# Playwright in pipeline Stage 2). --with-deps apt-installs the shared
# libraries Chromium needs on a slim base.
#
# PLAYWRIGHT_BROWSERS_PATH must be set BEFORE the install: browsers default
# to $HOME/.cache/ms-playwright, and the build runs as root while the
# container runs as `sourcemind` — root's cache would be unreadable at
# runtime. A shared world-readable path avoids that.
#
# Cost: adds roughly 400–500 MB to the image and several minutes to the build.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright \
    && rm -rf /var/lib/apt/lists/*

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

# Production: run Alembic migrations then start server
# In Kubernetes: migrations run as an init container, not here
# NOTE: the ["sh", "-c", ...] form is required, not cosmetic. ${PORT:-8000}
# is shell parameter expansion; with exec-form CMD (no shell) uvicorn would
# receive the literal string and exit with
#   Error: Invalid value for '--port': '${PORT:-8000}' is not a valid integer.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn sourcemind.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --no-access-log"]

# Health check for Docker / ECS
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

LABEL org.opencontainers.image.title="SourceMind API"
LABEL org.opencontainers.image.description="Collaborative team memory platform"
LABEL org.opencontainers.image.vendor="SourceMind"
