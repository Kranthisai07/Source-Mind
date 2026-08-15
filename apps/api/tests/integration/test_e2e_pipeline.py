"""
End-to-end ingestion pipeline test — the one test that proves the whole system.

This is deliberately NOT mockable. It exercises the real path:

    POST /v1/memories
      → receiver writes a Document + enqueues a Celery task
      → a real Celery worker (separate process) runs all 7 stages
      → Claude extracts facts, OpenAI embeds them
      → memories + attributions land in Postgres
      → POST /v1/memories/search finds them back

Every other test in this suite mocks the DB session, which is exactly why
the `:param::type` bind bug and the `Document.pipeline_data` column mismatch
survived 175 passing tests. This test would have caught both.

PREREQUISITES — this test fails fast (it does not hang) if they are missing:

  1. A live DATABASE_URL / REDIS_URL pointing at Railway (or any real
     Postgres + Redis pair). The skipif below gates on this.

  2. **A Celery worker must already be running in another process.** Start it
     with the queues named explicitly — a bare `celery worker` binds only the
     `default` queue while task_routes publishes to `ingestion`, so the task
     would sit unconsumed forever:

         cd apps/api
         .venv/Scripts/celery.exe -A sourcemind.workers.celery_app worker \
             --loglevel=info -Q default,ingestion,connectors --pool=solo

     `--pool=solo` is required on Windows. On Linux (i.e. the Railway worker
     service) drop it and use the default prefork pool.

  3. OPENAI_API_KEY and ANTHROPIC_API_KEY must be set — the pipeline makes
     real API calls. A run costs a few cents.

RUNTIME: expect 20–60s, and it GROWS with the number of memories already in
the workspace — relation detection classifies each new memory against its
nearest neighbours via an LLM call apiece. Override the ceiling with
E2E_TIMEOUT_SECONDS on a slow network or a large workspace.

REPEATABILITY: safe to run repeatedly. The dev org/user/workspace are seeded
with ON CONFLICT DO NOTHING, and each run embeds a UUID nonce in the payload
so the receiver's SHA-256 dedup doesn't short-circuit the pipeline.

CLEANUP: memories and the document are SOFT-deleted (deleted_at) at teardown.
They cannot be hard-deleted — `attributions` carries a BEFORE DELETE trigger
(`attribution_append_only`) that raises SM091, and memories cascade into it.
Attribution rows from test runs are therefore permanent by design.
"""

from __future__ import annotations

import os
import time
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

# Dev-bypass identities. These match the hardcoded UUID in
# core/dependencies.py's development auth bypass and the _DEV_WORKSPACE_ID
# default in api/v1/memories.py.
DEV_ORG_ID = "00000000-0000-4000-8000-000000000100"
DEV_USER_ID = "00000000-0000-4000-8000-000000000001"
DEV_WORKSPACE_ID = "00000000-0000-4000-8000-000000000010"

# 120s, not 30s. Ingestion time grows with workspace size: Stage 7b runs a
# pgvector top-10 neighbour search per new memory and an LLM classification
# for every pair inside the 0.20 threshold. An empty workspace completes in
# ~17s; once the dev workspace held ~50 memories the same payload took ~55s.
POLL_TIMEOUT_SECONDS = int(os.environ.get("E2E_TIMEOUT_SECONDS", "120"))
POLL_INTERVAL_SECONDS = 1.0


def _live_db_configured() -> bool:
    """True only when DATABASE_URL points at a real (non-local) Postgres."""
    from sourcemind.core.config import get_settings

    url = get_settings().database_url or ""
    return bool(url) and "localhost" not in url and "127.0.0.1" not in url


requires_live_stack = pytest.mark.skipif(
    not _live_db_configured(),
    reason=(
        "Requires live Railway DB + Redis and a running Celery worker. "
        "Start one with: celery -A sourcemind.workers.celery_app worker "
        "-Q default,ingestion,connectors --pool=solo"
    ),
)


@pytest_asyncio.fixture
async def committing_engine():
    """
    A COMMITTING engine — deliberately not the rollback-scoped `db_session`.

    The Celery worker runs in a separate process and can only see rows this
    test has actually committed, so the standard savepoint-rollback fixture
    is unusable here.
    """
    from sourcemind.core.config import get_settings

    engine = create_async_engine(
        get_settings().database_url,
        pool_size=2,
        max_overflow=0,
        pool_pre_ping=True,
    )
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def dev_identities(committing_engine):
    """
    Idempotently seed the org/user/workspace the dev auth bypass assumes.

    The bypass in core/dependencies.py returns a hardcoded user UUID but
    never provisions the row, and receiver.receive() requires the workspace
    to exist. ON CONFLICT DO NOTHING keeps repeat runs safe.
    """
    async with committing_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO organizations (id, name, slug) "
                "VALUES (CAST(:id AS uuid), 'Dev Org', 'dev-org') "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": DEV_ORG_ID},
        )
        await conn.execute(
            text(
                "INSERT INTO users (id, clerk_id, email, display_name) "
                "VALUES (CAST(:id AS uuid), 'dev_user_1', "
                "'dev@sourcemind.local', 'Dev User') "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": DEV_USER_ID},
        )
        await conn.execute(
            text(
                "INSERT INTO workspaces (id, organization_id, name, slug) "
                "VALUES (CAST(:id AS uuid), CAST(:org AS uuid), "
                "'Dev Workspace', 'dev-workspace') "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": DEV_WORKSPACE_ID, "org": DEV_ORG_ID},
        )
        await conn.execute(
            text(
                "INSERT INTO workspace_members (id, workspace_id, user_id, role) "
                "SELECT gen_random_uuid(), CAST(:ws AS uuid), "
                "CAST(:uid AS uuid), 'admin' "
                "WHERE NOT EXISTS (SELECT 1 FROM workspace_members "
                "WHERE workspace_id = CAST(:ws AS uuid) "
                "AND user_id = CAST(:uid AS uuid))"
            ),
            {"ws": DEV_WORKSPACE_ID, "uid": DEV_USER_ID},
        )
    return {"org": DEV_ORG_ID, "user": DEV_USER_ID, "workspace": DEV_WORKSPACE_ID}


@pytest_asyncio.fixture
async def api_client(monkeypatch):
    """
    In-process ASGI client with the development auth bypass active.

    The bypass triggers on `is_development and not clerk_secret_key`, so we
    blank the key on the cached Settings object. Running the app's lifespan
    initialises the DB and Redis pools the routes depend on.
    """
    import httpx

    from sourcemind.core.config import get_settings
    from sourcemind.main import app

    settings = get_settings()
    monkeypatch.setattr(settings, "clerk_secret_key", "", raising=False)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://e2e.local",
            timeout=60.0,
        ) as client:
            yield client


@pytest.mark.e2e
@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.asyncio
@requires_live_stack
async def test_full_ingestion_pipeline_end_to_end(
    api_client, committing_engine, dev_identities
):
    """
    Ingest one real document, wait for the worker, then search it back.

    Asserts, in order:
      1. POST /v1/memories returns 202 with a job_id
      2. the job reaches status="completed" within the timeout
      3. memory_count > 0
      4. every created memory has an embedding, a content_tsv, exactly one
         attribution and exactly one attribution_edit
      5. search returns at least one of those memories
    """
    nonce = uuid.uuid4().hex[:12]
    content = (
        f"Project Zephyr benchmark note {nonce}. "
        f"The Zephyr team selected Reciprocal Rank Fusion for hybrid retrieval "
        f"because it avoids normalizing pgvector cosine distance against BM25 "
        f"ranks, which live on incompatible scales. "
        f"Zephyr attribution weights character diff at 35 percent and semantic "
        f"similarity at 30 percent. Embeddings are stored as vector(3072)."
    )

    document_id: str | None = None
    try:
        # ── 1. Submit ────────────────────────────────────────────────────
        response = await api_client.post(
            "/v1/memories",
            params={"workspace_id": DEV_WORKSPACE_ID},
            headers={"Idempotency-Key": str(uuid.uuid4())},
            json={
                "content": content,
                "source_type": "text",
                "title": f"E2E pipeline test {nonce}",
            },
        )
        assert response.status_code == 202, (
            f"expected 202, got {response.status_code}: {response.text[:2000]}"
        )
        job = response.json()["data"]
        job_id = job["job_id"]
        document_id = job["document_id"]
        assert job["status"] in {"pending", "processing", "queued"}

        # ── 2. Poll to completion ────────────────────────────────────────
        deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
        final: dict | None = None
        seen: list[str] = []

        while time.monotonic() < deadline:
            poll = await api_client.get(f"/v1/memories/jobs/{job_id}")
            assert poll.status_code == 200, (
                f"job poll failed {poll.status_code}: {poll.text[:1000]}"
            )
            data = poll.json()["data"]
            if not seen or seen[-1] != data["status"]:
                seen.append(data["status"])
            if data["status"] == "completed":
                final = data
                break
            if data["status"] == "failed":
                pytest.fail(
                    f"ingestion FAILED: {data.get('error')} (stages seen: {seen})"
                )
            time.sleep(POLL_INTERVAL_SECONDS)

        assert final is not None, (
            f"job {job_id} did not complete within {POLL_TIMEOUT_SECONDS}s. "
            f"Stages observed: {seen}. "
            f"If it never left 'queued', no Celery worker is consuming the "
            f"'ingestion' queue — start one with "
            f"-Q default,ingestion,connectors."
        )

        # ── 3. Memories were produced ────────────────────────────────────
        assert final["memory_count"] > 0, f"completed with 0 memories: {final}"
        assert final["chunk_count"] >= 1

        # ── 4. Verify persisted state directly in the DB ─────────────────
        async with AsyncSession(committing_engine) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT id, embedding IS NOT NULL AS has_embedding, "
                        "content_tsv IS NOT NULL AS has_tsv "
                        "FROM memories "
                        "WHERE document_id = CAST(:doc AS uuid) "
                        "AND deleted_at IS NULL"
                    ),
                    {"doc": document_id},
                )
            ).all()

            assert len(rows) == final["memory_count"], (
                f"job reported {final['memory_count']} memories but "
                f"{len(rows)} rows are in the table"
            )

            for row in rows:
                assert row.has_embedding, f"memory {row.id} has a NULL embedding"
                assert row.has_tsv, f"memory {row.id} has a NULL content_tsv"

                counts = (
                    await session.execute(
                        text(
                            "SELECT "
                            "(SELECT count(*) FROM attributions "
                            " WHERE memory_id = CAST(:mid AS uuid)) AS attributions, "
                            "(SELECT count(*) FROM attribution_edits "
                            " WHERE memory_id = CAST(:mid AS uuid)) AS edits"
                        ),
                        {"mid": str(row.id)},
                    )
                ).one()
                assert counts.attributions == 1, (
                    f"memory {row.id}: expected 1 attribution, got "
                    f"{counts.attributions}"
                )
                assert counts.edits == 1, (
                    f"memory {row.id}: expected 1 attribution_edit, got "
                    f"{counts.edits}"
                )

            ingested_ids = {str(r.id) for r in rows}

        # ── 5 & 6. Search finds the ingested content ─────────────────────
        search = await api_client.post(
            "/v1/memories/search",
            params={"workspace_id": DEV_WORKSPACE_ID},
            json={
                "query": "Why did the Zephyr team choose Reciprocal Rank Fusion?",
                "mode": "hybrid",
                "limit": 10,
            },
        )
        assert search.status_code == 200, (
            f"search failed {search.status_code}: {search.text[:2000]}"
        )
        results = search.json()["results"]
        assert results, "search returned no results at all"

        returned_ids = {r["memory"]["id"] for r in results}
        overlap = ingested_ids & returned_ids
        assert overlap, (
            f"none of the {len(ingested_ids)} ingested memories appeared in "
            f"the top {len(results)} search results"
        )

    finally:
        # Soft-delete only. Hard deletes cascade into `attributions`, whose
        # BEFORE DELETE trigger (attribution_append_only) raises SM091.
        if document_id:
            async with committing_engine.begin() as conn:
                await conn.execute(
                    text(
                        "UPDATE memories SET deleted_at = NOW() "
                        "WHERE document_id = CAST(:doc AS uuid)"
                    ),
                    {"doc": document_id},
                )
                await conn.execute(
                    text(
                        "UPDATE documents SET deleted_at = NOW() "
                        "WHERE id = CAST(:doc AS uuid)"
                    ),
                    {"doc": document_id},
                )
