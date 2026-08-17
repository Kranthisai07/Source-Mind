# SourceMind — Architecture

> **AI-powered knowledge management for engineering teams.**
> Ingests GitHub artifacts, Discord conversations, and documents → extracts facts → builds an attribution-aware, conflict-detecting, searchable memory layer.

---

## 1. Layout

| Item | Value |
|------|-------|
| Monorepo root | `d:/Source Mind/` |
| API | `apps/api/` (FastAPI + Python 3.14 locally, 3.12 in Docker) |
| Web | `apps/web/` (Create React App + CRACO) |
| Evaluation harness | `evaluation/` |
| Local infra | `infra/docker-compose.yml` |
| Working branch | `dev` → PRs target `main` |

---

## 2. Python Environment

**Always use the venv — never the system Python.**

```bash
# Run anything
"apps/api/.venv/Scripts/python.exe" <script>

# Install packages (uv, not pip)
uv pip install --python apps/api/.venv/Scripts/python.exe <package>

# Tests
cd apps/api
.venv/Scripts/python.exe -m pytest tests/unit/ --no-cov -q
.venv/Scripts/python.exe -m pytest tests/integration/ --no-cov -q

# API server
cd apps/api
.venv/Scripts/python.exe -m uvicorn sourcemind.main:app --reload --port 8000

# Celery worker — the -Q flag is REQUIRED, see §4
cd apps/api
.venv/Scripts/celery.exe -A sourcemind.workers.celery_app worker \
    --loglevel=info -Q default,ingestion,connectors --pool=solo
```

`--pool=solo` is required on Windows. On Linux use the default prefork pool.

---

## 3. Status

| Area | State |
|------|-------|
| Foundation (DB, auth, health) | Complete |
| Ingestion pipeline (7-stage) | Complete, verified end to end |
| Attribution + conflicts + handoff + analytics | Complete; conflict/handoff paths lightly exercised |
| GitHub + Discord connectors | GitHub verified end to end; Discord has loader + mapper only, no orchestrator |
| Evaluation harness | Runs with 1 of 3 retrievers; see §12 |
| Frontend | Runs on mock data only; not wired to the API |

Full suite: `pytest --tb=short -q` → **176 passed, 6 skipped**.
Coverage is ~65% against a configured `--cov-fail-under=80`, so pytest exits non-zero.

The 6 skips are unit tests gated on `pg_ctl` being absent from PATH. The
end-to-end test skips instead when `DATABASE_URL` is unset or local.

---

## 4. Critical Gotchas — Read Before Touching Anything

### SQLAlchemy `text()` does not bind `:param::type`

This silently broke every raw-SQL query in the codebase until it was fixed.

```python
text("SELECT :p::text")          # binds -> []      ← :p passed through as literal SQL
text("SELECT CAST(:p AS text)")  # binds -> ['p']   ← correct
```

An unrecognised `:p` reaches Postgres verbatim and raises
`PostgresSyntaxError: syntax error at or near ":"`. **Always use
`CAST(:param AS type)` in raw SQL, never the `::` postfix cast on a bind
parameter.** Casts on columns (`content::text`) are fine.

### `metadata` is reserved in SQLAlchemy's Declarative API

Models needing a JSONB `metadata` column must rename the ORM attribute and
pass the real column name as the first argument to `mapped_column`:

- `User.clerk_data` → column `metadata`
- `Document.pipeline_data` → column `metadata`
- `ArtifactLink.artifact_metadata` → column `metadata`

Forgetting the override yields `column documents.pipeline_data does not exist`
at runtime. Raw SQL must also use the real column name (`metadata`), not the
ORM attribute.

### Celery: queue routing must be explicit

`task_routes` publishes to `ingestion` / `connectors`, but a bare
`celery worker` binds only `default`. Tasks then sit unconsumed **forever,
with no error**. Always pass `-Q default,ingestion,connectors`.

### Celery: use `@celery_app.task`, not `@shared_task`

`@shared_task` binds to Celery's `current_app`, which in the API process
resolves to a default app pointing at `localhost:6379` — publishing fails with
connection refused. Import the configured app:

```python
from sourcemind.workers.celery_app import app as celery_app
```

The instance in `workers/celery_app.py` is named `app`, not `celery_app`.

### Worker processes do not run the FastAPI lifespan

`get_engine()` and `get_redis()` raise `RuntimeError` unless `init_db()` /
`init_redis()` ran. A Celery task must build its own engine and call
`init_redis()` itself — see `workers/ingestion.py` for the pattern.

### Datetime columns need `DateTime(timezone=True)`

`type_annotation_map` does not map `datetime`, so a bare `Mapped[datetime]`
becomes `TIMESTAMP WITHOUT TIME ZONE` while every DB column is `timestamptz`.
Passing an aware datetime then raises *"can't subtract offset-naive and
offset-aware datetimes"*. `TimestampMixin` does this correctly; new models must
too.

### Alembic uses psycopg2, not asyncpg

`alembic/env.py` rewrites the URL to `postgresql+psycopg2://` for migrations.
`psycopg2-binary` is therefore a hard runtime dependency even though the app
uses asyncpg. SSL is required except on Railway's private network, which does
not terminate TLS.

### spaCy is unavailable in both environments

On Python 3.14 the import fails (spaCy uses Pydantic v1). On 3.12 the import
succeeds but `en_core_web_sm` is never downloaded, so `spacy.load` raises
`OSError`. Both sites (`scorer.py`, `chunker.py`) wrap load in `except
Exception` and fall back to regex, so behaviour is consistent. **Never load
real spaCy in tests** — mock `_sbert` / `_nlp` on the scorer instance.

### Module-level imports required for patching

Imports inside function bodies cannot be patched at module level.

### GitHub client specifics

- `get_discussions()` is a plain `async def` returning `list[dict]` — use
  `await`, never `async for`.
- `get_pull_requests` defaults to `state="all"`; `get_issues` defaults to
  `state="closed"`, `max_pages=10`. `_paginate` caps at 50 pages.
- `_paginate` uses a **fresh `httpx.AsyncClient` per page**. This is
  deliberate: a long-lived client inside an async generator blocks cleanup when
  the caller breaks early, hanging on Windows + Python 3.14.

---

## 5. Environment Variables (`apps/api/.env`)

```
# AI
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...

# Auth
CLERK_SECRET_KEY=...
CLERK_PUBLISHABLE_KEY=...

# Database — Railway Postgres.
#   *.railway.internal resolves ONLY from inside Railway.
#   For local work use the public proxy (*.proxy.rlwy.net).
DATABASE_URL=postgresql+asyncpg://postgres:<pw>@<host>:<port>/railway

# Redis — same internal/public split
REDIS_URL=redis://default:<pw>@<host>:<port>

# GitHub App (connector syncs)
GITHUB_APP_ID=...
GITHUB_APP_INSTALLATION_ID=...
GITHUB_APP_PRIVATE_KEY_PATH=...
GITHUB_WEBHOOK_SECRET=...

# GitHub PAT (evaluation dataset mining — broad public repo access)
GITHUB_RESEARCH_PAT=...

# Slack
SLACK_BOT_TOKEN=...
SLACK_SIGNING_SECRET=...
SLACK_APP_TOKEN=...
SLACK_DEFAULT_WORKSPACE_ID=

# Feature flags
FF_NEO4J_ATTRIBUTION=false
FF_KAFKA_EVENTS=false
FF_CONFLICT_DETECTION=true
```

Setting `ENVIRONMENT=production` additionally requires `SENTRY_DSN` or the app
refuses to boot — even though Sentry is never initialised anywhere.

`CORS_ORIGINS` defaults to localhost only; a deployed frontend needs it set.

---

## 6. File Map

```
apps/api/
  sourcemind/
    main.py                    FastAPI app, lifespan, exception handlers
    core/
      config.py                Settings via pydantic-settings (get_settings(), lru_cached)
      database.py              Async engine, get_db_session(), _build_engine()
      redis_client.py          Redis pool + cache helpers (get_redis() is SYNC)
      logging.py               Structlog (stdlib LoggerFactory — required)
      exceptions.py            SM001–SM099 error hierarchy
      middleware.py            CorrelationID, Logging, Timing, SecurityHeaders
      dependencies.py          FastAPI Depends + Clerk JWT verification
      graph.py                 Neo4j driver (disabled by feature flag)
    models/
      base.py                  Base + TimestampMixin, type_annotation_map (JSONB)
      user.py                  User (clerk_data → column `metadata`)
      organization.py          Organization
      workspace.py             Workspace, WorkspaceMember
      document.py              Document (pipeline_data → column `metadata`)
      memory.py                Memory + importance_score
      attribution.py           Attribution (append-only trigger), AttributionEdit
      memory_conflict.py       MemoryConflict
      memory_relation.py       MemoryRelation
      connector.py             ConnectorConfig, ConnectorSyncLog, ArtifactLink
    schemas/                   Pydantic v2 request/response models
    api/v1/
      health.py                GET /health (503 when a component is down)
      memories.py              Ingest, poll, get, patch, delete, versions
      search.py                POST /memories/search (hybrid RRF)
      workspaces.py            Workspace CRUD + members
      team.py                  GET /team/me + handoff endpoints
      conflicts.py             Conflict lifecycle
      analytics.py             Workspace analytics + who-would-know
      connectors.py            Connector CRUD + sync trigger + sync logs
      slack.py                 Slack events webhook
    connectors/
      github/
        app_auth.py            JWT (RS256) + Redis 50-min token cache
        pat_auth.py            Simple PAT holder
        client.py              Paginated REST + GraphQL
        mapper.py              ConnectorDocument + GitHubMapper
        connector.py           Sync orchestrator + dedup
      discord/
        dataset_loader.py      DiscordChatExporter JSON loader
        mapper.py              Conversation blocks → ConnectorDocument
    workers/
      celery_app.py            Celery instance (variable name: `app`)
      ingestion.py             7-stage pipeline task
      connector_tasks.py       sync_github_connector task
    services/
      ingestion/               receiver, extractor, chunker, fact_extractor, embedder
      attribution/             engine, scorer, versioning, handoff
      conflict/                resolver
      memory/                  store, relations, importance
      search/                  hybrid (RRF: pgvector + BM25)
      analytics/               workspace
      slack/                   bot, formatter
  alembic/versions/
    20250311_0001_initial_schema.py      Schema + HNSW + RLS + triggers
    20250312_0002_phase3_additions.py    importance_score, handoff, conflict cols
    20250415_0003_connectors.py          connector_configs, sync_logs, artifact_links
    20250816_0004_artifact_link_document_fk.py  ArtifactLink document_id + backfill
  tests/
    conftest.py                Rollback-scoped db_session, pg_available guard
    unit/                      Unit tests (mocked sessions)
    integration/
      test_e2e_pipeline.py     Full pipeline — needs a live DB and a worker

apps/web/                      React SPA (see §9)
evaluation/                    Benchmark harness (see §12)
infra/docker-compose.yml       Postgres, Redis, Neo4j, Redpanda, api, worker
```

Note: `handoff_records` and `handoff_assignments` exist in the database but
have **no ORM model** — they are reached only through raw SQL. The three
connector models are also absent from `models/__init__.py`, so Alembic
autogenerate would propose dropping those tables.

---

## 7. API Endpoints (v1)

All endpoints except `/health` require a Clerk Bearer JWT. In development with
`CLERK_SECRET_KEY` empty, a bypass returns a fixed mock user — it does **not**
create the row, so the user/workspace must already exist.

### Memories
```
POST   /v1/memories                    Ingest (202, async via Celery)
GET    /v1/memories/jobs/{id}          Poll ingestion status
POST   /v1/memories/search             Hybrid search (RRF)
GET    /v1/memories/{id}
PATCH  /v1/memories/{id}               New version + recomputed attribution
DELETE /v1/memories/{id}
GET    /v1/memories/{id}/versions      Recursive CTE version chain
```

`POST /v1/memories` requires an `Idempotency-Key` header containing a **UUID v4**.

### Connectors
```
POST   /v1/workspaces/{id}/connectors
GET    /v1/workspaces/{id}/connectors
GET    /v1/connectors/{id}
PATCH  /v1/connectors/{id}
DELETE /v1/connectors/{id}
POST   /v1/connectors/{id}/sync        Trigger sync (202)
GET    /v1/connectors/{id}/sync-logs
```

### Other
```
GET    /v1/workspaces  POST /v1/workspaces  GET /v1/workspaces/{id}
GET    /v1/workspaces/{id}/members
GET    /v1/workspaces/{id}/conflicts
GET    /v1/conflicts/{id}                     Detail + AI-suggested resolution
POST   /v1/conflicts/{id}/review
POST   /v1/conflicts/{id}/resolve             5 resolution types
POST   /v1/workspaces/{id}/handoff/initiate
POST   /v1/workspaces/{id}/handoff/assign
POST   /v1/workspaces/{id}/handoff/complete
GET    /v1/workspaces/{id}/analytics/overview
GET    /v1/workspaces/{id}/analytics/contribution-map
GET    /v1/workspaces/{id}/analytics/knowledge-gaps
GET    /v1/workspaces/{id}/analytics/who-would-know
GET    /v1/team/me
POST   /v1/slack/events
GET    /health
```

There is **no** `GET /v1/memories/{id}/attribution` endpoint, and no file-upload
endpoint — `PDFExtractor` is implemented but unreachable.

---

## 8. Key Algorithms

### 5-Signal Attribution — `services/attribution/scorer.py`

| Signal | Weight | Method |
|--------|--------|--------|
| S1 char_diff | 35% | Levenshtein edit ratio |
| S2 SBERT similarity | 30% | Sentence-BERT cosine |
| S3 temporal | 15% | Recency decay (0.8^position) |
| S4 NER overlap | 10% | spaCy if available, else regex |
| S5 approval | 10% | Explicit approval events |

Normalised to 1.0 with a 0.02 floor per contributor.

### Importance Score — `services/memory/importance.py`

Inbound relations 35%, approvals 25%, versions 20%, recency 10%, category 10%.

Recomputed on patch, relation creation, conflict resolution, and handoff
assignment — but **not** during ingestion, so freshly ingested memories keep
the database default.

### Hybrid Search — `services/search/hybrid.py`

pgvector cosine + BM25 `tsvector`, fused via Reciprocal Rank Fusion (k=60).
Thresholds: scan 0.25, LLM rerank 0.20, conflict detection 0.15.

`match_type` must be emitted as `"semantic+keyword"`; alphabetical sorting
produces `"keyword+semantic"`, which fails response validation.

### Connector Dedup

`ArtifactLink` is unique on `(workspace_id, source_tool, source_type,
source_id, memory_id)`, plus a partial unique index on the first four columns
`WHERE memory_id IS NULL` limiting each artifact to one pending anchor.

Links are created at sync time with `document_id` set and `memory_id` NULL,
then backfilled once ingestion produces memories — **one link row per
memory**. A document that yields no facts keeps its link at `memory_id` NULL.

### Handoff Tiers

Tier 1 = importance > 0.8 AND (sole contributor OR has inbound references);
Tier 2 = importance > 0.5; Tier 3 = the rest. Assignment transfers 40% of the
departing user's weight.

### Health Score

`0.30×coverage + 0.30×freshness + 0.25×conflict_ratio + 0.15×attribution_coverage`

---

## 9. Frontend

**Create React App 19 via CRACO** — not Next.js. React Router 7, Tailwind +
shadcn/ui (56 components), recharts, sonner.

State is local `useState`/`useEffect` only — no Redux, Context, or React Query.

`src/lib/api.js` dispatches between `mockApi` and `realApi` on
`REACT_APP_USE_MOCKS` (default `true`). **Mock mode is active**, so all 11
pages render from `src/lib/mockData.js`.

`realApi` is not usable as-is: it sends no `Authorization` header, uses
non-UUID workspace IDs, and several endpoint paths and response shapes do not
match the API.

Deployed to GitHub Pages from the `gh-pages` branch (compiled output only):

```bash
cd apps/web && npm run deploy      # build + gh-pages -d build
```

---

## 10. Database

**PostgreSQL 18 + pgvector**, `asyncpg` at runtime, `psycopg2` for migrations.

Tables: `organizations`, `users`, `workspaces`, `workspace_members`,
`documents`, `memories`, `attributions`, `attribution_edits`,
`memory_relations`, `memory_conflicts`, `handoff_records`,
`handoff_assignments`, `connector_configs`, `connector_sync_logs`,
`artifact_links`.

- `attributions` is **append-only** — a `BEFORE UPDATE OR DELETE` trigger
  raises SM091. Deleting a memory cascades into it and therefore also fails;
  use soft deletes (`deleted_at`).
- `memories.content_tsv` is a generated `tsvector` column (DDL only, not in the
  ORM), indexed with GIN for BM25.
- `memories.embedding` is `vector(3072)` with an HNSW index
  (`m=16, ef_construction=64`).
- Seven RLS policies exist but are **inert**: the application never sets
  `app.current_workspace_id`, and the connecting role owns the tables without
  `FORCE ROW LEVEL SECURITY`. Isolation depends entirely on `WHERE
  workspace_id = …` in queries.

---

## 11. End-to-End Test

`tests/integration/test_e2e_pipeline.py` is the only test that exercises the
real pipeline. It is not mockable by design.

```bash
# Terminal 1 — worker must be running
cd apps/api && .venv/Scripts/celery.exe -A sourcemind.workers.celery_app worker \
    --loglevel=info -Q default,ingestion,connectors --pool=solo

# Terminal 2
cd apps/api && .venv/Scripts/python.exe -m pytest tests/integration/test_e2e_pipeline.py -q
```

Needs a live database, Redis, and both AI API keys. A run costs a few cents and
takes 20–60s — longer as the workspace grows, because relation detection makes
one LLM call per nearby memory pair. Override the ceiling with
`E2E_TIMEOUT_SECONDS`.

Skips cleanly when `DATABASE_URL` is unset or points at localhost.

---

## 12. Evaluation Harness

```bash
# 1. Build ground truth (mines 5 public repos via PAT)
python -m evaluation.dataset_builder --output evaluation/data/ground_truth.json --limit 20

# 2. Run
python -m evaluation.runner --ground-truth evaluation/data/ground_truth.json \
    --output evaluation/data/results.json

# 3. Report
python -m evaluation.report --results evaluation/data/results.json \
    --output evaluation/data/report.md
```

`evaluation/data/ground_truth.json` holds 300 mined artifacts (100 commits,
100 PRs, 80 issues, 20 discussions) across react, TypeScript, rust, next.js and
django.

**Current limitations** — worth knowing before quoting any numbers:

- Only NaiveRAG runs by default. Supermemory needs `SUPERMEMORY_API_KEY` and is
  skipped **silently**. SourceMind needs `SOURCEMIND_API_URL` / `_API_KEY` /
  `_WORKSPACE_ID`, and there is no script that ingests the dataset into it.
- Attribution accuracy reports 0.000 because the NaiveRAG baseline does not
  store `author` in its vector metadata.
- Conflict detection never runs: the dataset has no `metadata.state` on issues,
  and the runner hardcodes a uniform "no conflict" prediction.
- Knowledge retention, latency, and role-scoped retrieval are meaningful.

---

## 13. Deployment

**Two services** are deployed from this one repository, both building
`apps/api/Dockerfile` at the `production` target with root directory
`apps/api`. Each names its own config file, because an auto-detected
`railway.json` would apply to both — and the worker would inherit the API's
`healthcheckPath`, which it can never satisfy.

| Service | Config file | Public domain | Healthcheck |
|---------|-------------|---------------|-------------|
| API | `apps/api/railway.api.json` | yes | `/health` |
| Worker | `apps/api/railway.worker.json` | no | none |

**API start command:**
```sh
sh -c 'alembic upgrade head && exec uvicorn sourcemind.main:app \
    --host 0.0.0.0 --port ${PORT:-8000} --workers 1'
```

**Worker start command:**
```sh
sh -c 'celery -A sourcemind.workers.celery_app worker --loglevel=info \
    -Q default,ingestion,connectors --concurrency=2'
```

**Dockerfile CMD** (used only when no config file applies):
```dockerfile
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn sourcemind.main:app \
    --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --no-access-log"]
```

### `sh -c` is mandatory, not stylistic

Railway tokenizes `startCommand` and execs it **without a shell**, so
`${PORT:-8000}` is passed through literally and uvicorn exits immediately:

```
Error: Invalid value for '--port': '${PORT:-8000}' is not a valid integer.
```

That failure is nearly invisible — alembic still succeeds, uvicorn never
prints a banner, and the healthcheck simply reports the service as
unavailable for its whole window. Wrapping in `sh -c` supplies the shell
that performs the expansion. The Dockerfile `CMD` uses the
`["sh", "-c", ...]` form for the same reason; converting it to exec form
reintroduces the bug.

### No EXPOSE in the production stage

The server binds whatever `$PORT` the platform injects, so a hardcoded
`EXPOSE` is a *wrong* hint rather than a redundant one. Platforms use it to
infer the target port: `EXPOSE 8000` against an app listening on an injected
8080 routed the edge to a dead port, so the healthcheck passed internally
while every public request returned 502.

### Environment variables

Nothing from `.env` reaches the container — the Dockerfile never copies it.
Every variable must be set in the platform dashboard, and **the API and
worker services need identical values**, particularly `DATABASE_URL` and
`REDIS_URL`. If they diverge, the API enqueues to one broker while the
worker consumes from another: jobs sit at `queued` forever with no error,
and the worker looks healthy and idle. Prefer reference variables
(`${{Postgres.DATABASE_URL}}`) so the two cannot drift.

`ENVIRONMENT=production` additionally requires `SENTRY_DSN` or the app
refuses to boot. `staging` behaves identically to production in every other
respect — auth enforced, docs hidden, tracebacks suppressed — and skips only
that check.

### Remaining gaps

- Each uvicorn worker opens its own pool (20 + 10 overflow); check the total
  against the database connection limit before raising `--workers`.
- The Docker `HEALTHCHECK` hardcodes port 8000 while the server binds
  `$PORT`. Railway uses `healthcheckPath` instead, so this is inert there,
  but it is wrong for any runtime that honours `HEALTHCHECK`.
- Playwright Chromium is installed in the production image with
  `PLAYWRIGHT_BROWSERS_PATH=/ms-playwright` so the non-root user can read it.
  The development stage has no browsers, so URL ingestion fails there.

---

## 14. Local Infrastructure

```bash
docker compose -f infra/docker-compose.yml up -d
```

Starts Postgres (pgvector), Redis, Neo4j, Redpanda, the API, and a worker.
Neo4j and Redpanda are disabled by feature flags and can be skipped.

---

## 15. Debugging

```bash
# Migration state
cd apps/api && .venv/Scripts/python.exe -m alembic current
cd apps/api && .venv/Scripts/python.exe -m alembic history

# Clear the settings cache after changing .env in a live process
from sourcemind.core.config import get_settings
get_settings.cache_clear()

# Check GitHub PAT rate limit
python -c "
import httpx, asyncio
async def f():
    async with httpx.AsyncClient() as c:
        r = await c.get('https://api.github.com/rate_limit',
                        headers={'Authorization': 'Bearer <PAT>'})
        print(r.json()['resources']['core'])
asyncio.run(f())
"
```

---

## 16. Adding a New Connector

1. Create `sourcemind/connectors/<name>/` with `__init__.py`.
2. Implement an auth class exposing `async def get_token() -> str`.
3. Implement a mapper returning `ConnectorDocument`, using
   `ConnectorDocument.make_key()` for the idempotency key.
4. Implement an orchestrator: call `receive()` for new artifacts and insert an
   `ArtifactLink` with `document_id` set and `memory_id` NULL.
5. Add a Celery task in `workers/connector_tasks.py` using `@celery_app.task`.
6. Add the type to `ConnectorTypeLiteral` **and** to the `connector_type` CHECK
   constraint in the database — the schema currently allows only `github` and
   `discord`, so `slack`/`notion` pass validation and fail on insert.
7. Write unit tests under `tests/unit/connectors/`.
