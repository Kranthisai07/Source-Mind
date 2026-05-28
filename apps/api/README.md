# SourceMind API

AI-powered knowledge management for engineering teams. Ingests GitHub
artifacts, Discord conversations, and documents → extracts facts → builds an
attribution-aware, conflict-detecting, searchable memory layer.

FastAPI + Python 3.14 + PostgreSQL 16 (pgvector) + Redis + Celery.

---

## Architecture

The codebase follows a strict layered design — the FastAPI route layer is
thin, delegating all business logic to the `services/` package. Services
never import from `api/`. Persistence goes through SQLAlchemy 2.0 (async)
against PostgreSQL 16 with the `pgvector` extension for HNSW similarity
search. Background work runs on Celery with Redis as the broker.

```
sourcemind/
├── api/          # FastAPI routers (HTTP transport only)
├── core/         # Cross-cutting: config, db, redis, auth, exceptions
├── models/       # SQLAlchemy ORM models
├── schemas/      # Pydantic v2 request/response models
├── services/     # All business logic
├── connectors/   # External-system adapters (GitHub, Discord, ...)
└── workers/      # Celery task entry points
```

## Services

- **`ingestion/`** — 7-stage async pipeline:
  RECEIVE → EXTRACT → CHUNK → EXTRACT_FACTS → EMBED → INDEX → RELATE.
  Each stage is testable in isolation; the orchestrator lives in
  `workers/ingestion.py`. Tree-sitter for code, Playwright for URLs,
  PyMuPDF for PDFs, Claude for atomic-fact extraction, OpenAI
  `text-embedding-3-large` (3072-dim) for vectors.

- **`attribution/`** — 5-signal contribution scoring (see
  [ADR-007](../../docs/architecture/ADR-007-multi-signal-attribution-algorithm.md)):
  char-diff (35%) + SBERT semantic survival (30%) + temporal primacy (15%) +
  structural NER (10%) + explicit approval (10%). Records are append-only,
  enforced by a Postgres trigger ([ADR-002](../../docs/architecture/ADR-002-attribution-append-only.md)).
  Includes the handoff workflow that tiers a departing user's memories and
  transfers 40% of their attribution share to a successor.

- **`search/`** — Hybrid retrieval combining pgvector cosine similarity and
  PostgreSQL `tsvector` BM25, fused via Reciprocal Rank Fusion (RRF,
  k=60). See [ADR-005](../../docs/architecture/ADR-005-hybrid-search-rrf.md).
  Latency target: p50 < 200ms, p95 < 500ms.

- **`conflict/`** — Detects contradictions between memories during ingestion
  (cosine distance < 0.15 from different contributors) and runs the
  resolution lifecycle: open → under_review → resolved | deferred. Claude
  Haiku generates non-binding resolution suggestions.

- **`memory/`** — `store` writes memories from ingestion, `relations`
  detects semantic relationships between memories (updates / extends /
  derives), and `importance` recomputes the per-memory importance score
  (inbound relations, approvals, version count, recency, category).

- **`analytics/`** — Workspace health score (coverage + freshness +
  conflict ratio + attribution coverage), contribution map, knowledge gaps,
  and "who would know?" reverse-expertise search over BM25 + attribution.

- **`slack/`** — Slack Bolt bot exposing `/sourcemind <query>` for hybrid
  search and `/sourcemind who knows <topic>` for expertise lookup, plus
  rich Block Kit formatters.

## Connectors

- **`github/`** — GitHub App authentication (JWT RS256, installation tokens
  cached in Redis for 50 min) + paginated REST/GraphQL client. Maps four
  artifact types — commits, pull requests, issues, discussions — into the
  generic `ConnectorDocument` ingestion envelope.

- **`discord/`** — Loader for [DiscordChatExporter](https://github.com/Tyrrrz/DiscordChatExporter)
  JSON exports (used for offline research datasets). Groups messages into
  conversation blocks (30-min windows, ≤8 000 chars) before ingestion.

Dedup across connectors is enforced by the `artifact_links` unique
constraint on `(workspace_id, source_tool, source_type, source_id)`.

## Running tests

```bash
# Always use the venv
.venv/Scripts/python.exe -m pytest --no-cov -q

# Unit tests only
.venv/Scripts/python.exe -m pytest tests/unit/ --no-cov -q

# A specific module
.venv/Scripts/python.exe -m pytest tests/unit/attribution/ --no-cov -q
```

Expected: **169 passed, 12 skipped**. The 12 skips are 6 real-DB
integration tests (require local Postgres + pgvector) and 6 auth tests
that need a Clerk dev key.

## Running the API

```bash
.venv/Scripts/python.exe -m uvicorn sourcemind.main:app --reload --port 8000
```

Open <http://localhost:8000/docs> for the OpenAPI explorer.

For ingestion to actually complete, a Celery worker also needs to be
running against the same Redis broker:

```bash
.venv/Scripts/python.exe -m celery -A sourcemind.workers.celery_app worker -l info
```

## Environment

All configuration is loaded from `.env` via `pydantic-settings`. See
[`.env.example`](../../.env.example) for the full list. Required for
production startup (enforced by `validate_production_settings` in
`core/config.py`):

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | Embeddings + dedup |
| `ANTHROPIC_API_KEY` | Fact extraction + conflict suggestions |
| `CLERK_SECRET_KEY` | JWT verification + user provisioning |
| `SENTRY_DSN` | Error reporting |
| `DATABASE_URL` | Postgres + pgvector (Supabase pooler) |
| `REDIS_URL` | Cache + Celery broker (Upstash TLS) |

Connector-specific secrets (`GITHUB_APP_*`, `SLACK_*`) are only required
when their respective integration is wired up.

## Project root

The repository root [`CLAUDE.md`](../../CLAUDE.md) is the canonical guide
to gotchas — read it before touching SQLAlchemy models, the test layout,
or anything spaCy-adjacent.
