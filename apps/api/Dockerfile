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

COPY pyproject.toml ./

# Install all dependencies (including dev in this stage for layer caching)
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

# Copy only production dependencies (no dev extras)
COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir hatchling \
    && pip install --no-cache-dir -e "."

# Copy application source
COPY sourcemind/ ./sourcemind/
COPY alembic/ ./alembic/
COPY alembic.ini ./

# Security: own files as root, run as non-root
RUN chown -R sourcemind:sourcemind /app
USER sourcemind

ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV ENVIRONMENT=production

EXPOSE 8000

# Production: run Alembic migrations then start server
# In Kubernetes: migrations run as an init container, not here
CMD ["sh", "-c", "alembic upgrade head && uvicorn sourcemind.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 4 --no-access-log"]

# Health check for Docker / ECS
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

LABEL org.opencontainers.image.title="SourceMind API"
LABEL org.opencontainers.image.description="Collaborative team memory platform"
LABEL org.opencontainers.image.vendor="SourceMind"
