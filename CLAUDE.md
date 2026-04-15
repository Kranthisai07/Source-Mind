# CLAUDE.md — SourceMind

> **AI-powered knowledge management for engineering teams.**  
> Ingests GitHub artifacts, Discord conversations, and documents → extracts facts → builds an attribution-aware, conflict-detecting, searchable memory layer.

---

## 1. Orientation

| Item | Value |
|------|-------|
| Monorepo root | `d:/Source Mind/` |
| Primary app | `apps/api/` (FastAPI + Python 3.14) |
| Frontend | `apps/web/` (Next.js 15 + Clerk) |
| Evaluation harness | `evaluation/` |
| Git branch (dev) | `dev` → PRs target `main` |
| Git user | `Kranthisai07` |

---

## 2. Python Environment

**Always use the venv — never the system Python.**

```bash
# Run anything
d:/Source Mind/apps/api/.venv/Scripts/python.exe <script>

# Install packages (uv, not pip)
uv pip install --python apps/api/.venv/Scripts/python.exe <package>

# Run tests
cd apps/api
.venv/Scripts/python.exe -m pytest tests/unit/ --no-cov -q
.venv/Scripts/python.exe -m pytest tests/integration/ --no-cov -q

# Run the API server
cd apps/api
.venv/Scripts/python.exe -m uvicorn sourcemind.main:app --reload --port 8000
```

---

## 3. Project Phase Status

| Phase | Status | Tests |
|-------|--------|-------|
| Phase 1 — Foundation (DB, auth, health) | ✅ COMPLETE | 26 pass |
| Phase 2 — Ingestion Pipeline (7-stage) | ✅ COMPLETE | 59 pass |
| Phase 3 — Attribution + Conflicts + Handoff + Analytics | ✅ COMPLETE | 97 pass |
| Hardening Sprint — NER fallback, importance score, typed responses | ✅ COMPLETE | — |
| Phase A — GitHub + Discord connectors | ✅ COMPLETE | 27 pass |
| Phase D — Evaluation harness (5 metrics, 3 baselines) | ✅ COMPLETE | 12 pass |
| **Total** | | **163 unit + 6 integration** |

Run the full suite: `pytest tests/ --no-cov -q` → expect **163 passed, 6 skipped** (real-DB tests skip when `pg_ctl` absent).

---

## 4. Critical Gotchas — Read Before Touching Anything

### SQLAlchemy reserved names
`metadata` is a **reserved attribute name** in SQLAlchemy's Declarative API. Every model that needs a JSONB metadata column must rename the ORM attribute:
- `User.clerk_data` (maps to DB column `clerk_data`)
- `Document.pipeline_data` (maps to DB column `pipeline_data`)
- `ArtifactLink.artifact_metadata` (maps to DB column `metadata` via `mapped_column("metadata", ...)`)

### spaCy is broken on Python 3.14
spaCy uses Pydantic v1 internally which raises a config error on Python 3.14. **Never import spaCy in tests.** The scorer uses a regex NER fallback (`_TECH_PATTERN` in `scorer.py`). Always mock `scorer._sbert` and `scorer._nlp` directly on the `AttributionScorer` instance in tests.

### pytest-postgresql is uninstalled
It was removed because its psycopg dependency requires libpq which isn't available on this Windows machine. The `pg_available` skip guard in `tests/conftest.py` handles this automatically — 6 real-DB tests skip cleanly. **Do not reinstall pytest-postgresql.**

### Module-level imports required for patching
Imports inside function bodies cannot be patched at module level. If you add a new service that needs patching in tests, ensure the import is at the top of the module.

### Celery app variable name
In `workers/celery_app.py`, the Celery instance is named `app` (not `celery_app`). Import it as: `from sourcemind.workers.celery_app import app as celery_app`.

### GitHub client: `get_discussions` returns a list
`GitHubClient.get_discussions()` is a regular `async def` that returns `list[dict]`, **not** an async generator. Use `await client.get_discussions(...)`, never `async for`.

### GitHub client: default states
- `get_pull_requests` defaults to `state="all"`, no sort (sort only applied when `since` is set)
- `get_issues` defaults to `state="closed"`, `max_pages=10` — prevents infinite pagination on PR-heavy repos like django/django
- `_paginate` has `max_pages=50` hard cap

### AsyncIO generator cleanup on Windows
The `_paginate` method in `GitHubClient` uses a **fresh `httpx.AsyncClient` per page** (not one shared client for all pages). This is intentional — a long-lived client inside an async generator blocks cleanup when the caller `break`s early, causing hangs on Python 3.14 + Windows asyncio.

---

## 5. Environment Variables (apps/api/.env)

```
# AI
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...

# Auth
CLERK_SECRET_KEY=...
CLERK_PUBLISHABLE_KEY=...

# Database (Supabase session pooler)
DATABASE_URL=postgresql+asyncpg://postgres.<project>:<password>@aws-1-us-east-2.pooler.supabase.com:5432/postgres

# Redis (Upstash TLS)
REDIS_URL=rediss://...

# GitHub App (for connector syncs — only has access to Kranthisai07/Source-Mind)
GITHUB_APP_ID=3382865
GITHUB_APP_INSTALLATION_ID=124061938
GITHUB_APP_PRIVATE_KEY_PATH=D:\Source Mind\apps\api\sourcemind-kranthi.2026-04-14.private-key.pem
GITHUB_WEBHOOK_SECRET=sourcemind_research

# GitHub PAT (for evaluation dataset mining — broad public repo access)
GITHUB_RESEARCH_PAT=...

# Feature flags
FF_NEO4J_ATTRIBUTION=false
FF_KAFKA_EVENTS=false
FF_CONFLICT_DETECTION=true
```

**GitHub App scope:** The App installation (`124061938`) only has access to `Kranthisai07/Source-Mind`. For mining public repos (django, react, etc.) use `GitHubPATAuth` with `GITHUB_RESEARCH_PAT`.

---

## 6. File Map

```
apps/api/
  sourcemind/
    main.py                    FastAPI app, lifespan, exception handlers
    core/
      config.py                Settings via pydantic-settings (get_settings())
      database.py              Async SQLAlchemy engine, get_db_session()
      redis_client.py          Redis pool, cache helpers
      logging.py               Structlog (stdlib LoggerFactory — required)
      exceptions.py            SM001–SM099 error hierarchy
      middleware.py            CorrelationID, Logging, Timing, SecurityHeaders
      dependencies.py          FastAPI Depends: CurrentUser, DBSession, etc.
      graph.py                 Neo4j async driver (FF_NEO4J_ATTRIBUTION=false)
    models/
      base.py                  Base + TimestampMixin, type_annotation_map (JSONB)
      user.py                  User (clerk_data — NOT metadata)
      workspace.py             Workspace, WorkspaceMember
      document.py              Document (pipeline_data — NOT metadata)
      memory.py                Memory + importance_score field
      attribution.py           Attribution (append-only trigger)
      conflict.py              Conflict + ConflictStatus enum
      handoff.py               HandoffRecord
      connector.py             ConnectorConfig, ConnectorSyncLog, ArtifactLink
    schemas/
      *.py                     Pydantic v2 request/response schemas
    api/v1/
      health.py                GET /health
      memories.py              POST/GET/PATCH/DELETE /memories + versions
      search.py                POST /memories/search (hybrid RRF)
      workspaces.py            Workspace CRUD
      attribution.py           GET /memories/:id/attribution
      team.py                  GET /team/me + handoff endpoints
      conflicts.py             Conflict lifecycle endpoints
      analytics.py             Workspace analytics endpoints
      connectors.py            Connector CRUD + sync trigger + sync logs
    connectors/
      github/
        app_auth.py            GitHubAppAuth — JWT (RS256) + Redis 50-min cache
        pat_auth.py            GitHubPATAuth — simple PAT, no cache
        client.py              GitHubClient — paginated REST + GraphQL
        mapper.py              ConnectorDocument + GitHubMapper (4 artifact types)
        connector.py           GitHubConnector — sync orchestrator + dedup
      discord/
        dataset_loader.py      DiscordDatasetLoader — DiscordChatExporter JSON
        mapper.py              DiscordMapper — blocks → ConnectorDocument
    workers/
      celery_app.py            Celery app (var: `app`), queues: ingestion/attribution/connectors
      ingestion.py             7-stage pipeline Celery task
      connector_tasks.py       sync_github_connector Celery task
    services/
      ingestion/               receiver, extractor, chunker, fact_extractor, embedder
      attribution/             engine, scorer, versioning, handoff
      conflict/                resolver
      memory/                  store, relations, importance
      search/                  hybrid (RRF pgvector + BM25)
      analytics/               workspace
  alembic/versions/
    20250311_0001_initial_schema.py     Full schema + HNSW + RLS + triggers
    20250312_0002_phase3_additions.py   importance_score, handoff, conflict cols
    20250415_0003_connectors.py         connector_configs, sync_logs, artifact_links
  tests/
    conftest.py                pg_available skip guard + anyio_backend fixture
    unit/                      163 tests across all modules
    integration/               6 tests (3 attribution, 2 conflict, 1 handoff)

evaluation/
  dataset_builder.py           Mines 5 repos → ground_truth.json (uses PAT)
  baselines/
    naive_rag.py               Chroma + OpenAI embeddings
    supermemory_baseline.py    Supermemory REST API
  metrics/
    knowledge_retention.py     recall@k
    attribution_accuracy.py    author attribution accuracy
    conflict_detection.py      precision/recall/F1 + pair builder
    role_scoped_retrieval.py   engineer vs manager scope
    latency.py                 p50/p95/p99 benchmarking
  runner.py                    Runs all baselines × all metrics
  report.py                    Markdown comparison table
```

---

## 7. API Endpoints (v1)

### Memories
```
POST   /v1/memories                          Ingest (async 202, Celery)
GET    /v1/memories/jobs/{id}                Poll ingestion status
POST   /v1/memories/search                   Hybrid search (RRF pgvector + BM25)
GET    /v1/memories/{id}
DELETE /v1/memories/{id}
PATCH  /v1/memories/{id}                     Creates new version + recomputes attribution
GET    /v1/memories/{id}/versions            Recursive CTE version chain
GET    /v1/memories/{id}/attribution
```

### Connectors
```
POST   /v1/workspaces/{id}/connectors        Create connector
GET    /v1/workspaces/{id}/connectors        List connectors
GET    /v1/connectors/{id}
PATCH  /v1/connectors/{id}
DELETE /v1/connectors/{id}
POST   /v1/connectors/{id}/sync              Trigger sync (202, Celery)
GET    /v1/connectors/{id}/sync-logs         Paginated sync history
```

### Other
```
GET    /v1/workspaces/{id}/conflicts         List with status filter
GET    /v1/conflicts/{id}                    Detail + Claude Haiku suggestion
POST   /v1/conflicts/{id}/review             open → under_review
POST   /v1/conflicts/{id}/resolve            5 resolution types
POST   /v1/workspaces/{id}/handoff/initiate  Classify + create record
POST   /v1/workspaces/{id}/handoff/assign    40% attribution transfer
POST   /v1/workspaces/{id}/handoff/complete  Mark departed
GET    /v1/workspaces/{id}/analytics/overview         Health score (Redis 300s cache)
GET    /v1/workspaces/{id}/analytics/contribution-map
GET    /v1/workspaces/{id}/analytics/knowledge-gaps
GET    /v1/team/me
GET    /health
```

---

## 8. Key Algorithms

### 5-Signal Attribution
`services/attribution/scorer.py`
| Signal | Weight | Method |
|--------|--------|--------|
| S1 char_diff | 35% | Character-level edit ratio |
| S2 SBERT similarity | 30% | Sentence-BERT cosine (mocked in tests) |
| S3 temporal | 15% | Recency decay |
| S4 NER overlap | 10% | Regex fallback (`_TECH_PATTERN`) — spaCy disabled on Py3.14 |
| S5 approval | 10% | Explicit approval events |
Floor normalization: minimum score = 0.02

### Importance Score
`services/memory/importance.py`
Recomputed on: PATCH memory, relation creation, conflict resolution, handoff assignment.
| Signal | Weight |
|--------|--------|
| Inbound relations | 35% |
| Approval count | 25% |
| Version count | 20% |
| Recency | 10% |
| Category | 10% |

### Hybrid Search (RRF)
`services/search/hybrid.py` — pgvector cosine + BM25 tsvector, fused via Reciprocal Rank Fusion.
Thresholds: scan=0.25, LLM rerank=0.20, conflict detection=0.15

### Connector Dedup
`ArtifactLink` unique constraint on `(workspace_id, source_tool, source_type, source_id)`.
`ConnectorDocument.idempotency_key` = SHA-256(`[tool, type, id]`) for upstream dedup in receiver.

### Handoff Tiers
- Tier 1: importance > 0.8 AND solo author → critical, immediate action
- Tier 2: importance > 0.5 → important
- Tier 3: rest → standard
40% attribution transferred on assign.

### Health Score
`0.30×coverage + 0.30×freshness + 0.25×conflict_ratio + 0.15×attribution_coverage`

---

## 9. Database

**Supabase (PostgreSQL 16 + pgvector)**  
Connection via session pooler (IPv4-compatible, port 5432). Uses `asyncpg` driver.

Key tables: `users`, `workspaces`, `workspace_members`, `documents`, `memories`, `attributions`, `conflicts`, `handoff_records`, `connector_configs`, `connector_sync_logs`, `artifact_links`

Notable: `attributions` is **append-only** (DB trigger `attribution_append_only`). Never UPDATE or DELETE from it.

`content_tsv` is a generated tsvector column used for BM25 — added via DDL in migration 0001, not via ORM.

---

## 10. Running the Evaluation

```bash
# Step 1 — build ground truth (mines 5 public repos via PAT)
cd "d:/Source Mind"
python -m evaluation.dataset_builder --output evaluation/data/ground_truth.json --limit 20

# Step 2 — run evaluation (set env vars first)
OPENAI_API_KEY=... python -m evaluation.runner \
  --ground-truth evaluation/data/ground_truth.json \
  --output evaluation/data/results.json

# Step 3 — generate report
python -m evaluation.report \
  --results evaluation/data/results.json \
  --output evaluation/data/report.md
```

For SourceMind baseline, set: `SOURCEMIND_API_URL`, `SOURCEMIND_API_KEY`, `SOURCEMIND_WORKSPACE_ID`  
For Supermemory baseline, set: `SUPERMEMORY_API_KEY`

---

## 11. Adding a New Connector

1. Create `sourcemind/connectors/<name>/` with `__init__.py`
2. Implement auth class with `async def get_token() -> str`
3. Implement mapper: `from_<artifact>(raw) -> ConnectorDocument` — use `ConnectorDocument.make_key()` for idempotency key
4. Implement orchestrator: call `receive()` for new artifacts, insert `ArtifactLink` for dedup
5. Add Celery task in `workers/connector_tasks.py`
6. Add API endpoints in `api/v1/connectors.py` (already generic — may not need changes)
7. Write unit tests under `tests/unit/connectors/`

---

## 12. Common Debugging

```bash
# Check what the GitHub App installation can access
python -c "
import jwt, time, httpx, asyncio
pem = open('apps/api/sourcemind-kranthi.2026-04-14.private-key.pem').read()
token = jwt.encode({'iat': int(time.time())-60, 'exp': int(time.time())+540, 'iss': '3382865'}, pem, algorithm='RS256')
async def f():
    async with httpx.AsyncClient() as c:
        r = await c.post('https://api.github.com/app/installations/124061938/access_tokens',
            headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json'})
        t = r.json()['token']
        r2 = await c.get('https://api.github.com/installation/repositories',
            headers={'Authorization': f'Bearer {t}', 'Accept': 'application/vnd.github+json'})
        print(r2.json())
asyncio.run(f())
"

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

# Clear settings cache in tests
from sourcemind.core.config import get_settings
get_settings.cache_clear()

# Check Alembic migration state
cd apps/api && .venv/Scripts/python.exe -m alembic current
cd apps/api && .venv/Scripts/python.exe -m alembic history
```

---

## 13. Infrastructure (Local Dev)

```bash
# Start all services (Postgres, Redis, Neo4j, Redpanda)
docker compose -f infra/docker-compose.yml up -d

# Neo4j and Kafka/Redpanda are DISABLED by feature flags
# FF_NEO4J_ATTRIBUTION=false, FF_KAFKA_EVENTS=false
# Safe to skip starting them for most development
```

**Live services used in dev:**
- Database: Supabase cloud (see `DATABASE_URL` in `.env`)
- Redis: Upstash cloud TLS (see `REDIS_URL` in `.env`)
- Auth: Clerk (dev instance)
