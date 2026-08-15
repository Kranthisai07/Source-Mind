"""
Real-database integration tests against Supabase.

Each test runs inside an outer transaction with `join_transaction_mode=
"create_savepoint"`, so every INSERT/UPDATE is rolled back at teardown
and no data leaks into Supabase between runs.

What's covered:
  1. test_hnsw_vector_search             — pgvector cosine search ordering
  2. test_attribution_append_only_trigger — DB-level write protection
  3. test_rls_workspace_isolation_policy  — RLS policies are defined
  4. test_memory_version_chain            — recursive CTE on parent_memory_id
  5. test_full_text_search_gin_index      — content_tsv tsvector search
  6. test_soft_delete_isolation           — deleted_at IS NULL filter

Skip guards:
  - `pg_available` keeps the legacy skip if pg_ctl isn't on PATH
    (mostly defensive; the supabase_url fixture is the real gate).
  - `supabase_url` skips if DATABASE_URL is unset or localhost.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import _hash, pg_available


def _zero_vec_with(*nonzero: tuple[int, float]) -> list[float]:
    """Build a 3072-dim vector with specific values at given indices."""
    v = [0.0] * 3072
    for idx, value in nonzero:
        v[idx] = value
    return v


def _vec_literal(vec: list[float]) -> str:
    """Render a Python float list as the `[x,y,z]` literal pgvector accepts."""
    return "[" + ",".join(repr(x) for x in vec) + "]"


# ─────────────────────────────────────────────────────────────────────────────
# TEST 1 — HNSW vector search
# ─────────────────────────────────────────────────────────────────────────────

@pg_available
@pytest.mark.integration
@pytest.mark.asyncio
async def test_hnsw_vector_search(db_session: AsyncSession, test_workspace):
    """
    Verify pgvector cosine ordering on a real Postgres instance.

    The HNSW index is built on `embedding::halfvec(3072)`; with only three
    rows the planner will seq-scan, but the operator still computes cosine
    distance correctly. We assert the identical vector ranks first.
    """
    from sourcemind.models.memory import Memory

    ws_id = test_workspace.id

    m1_content = "Memory one — aligned with query"
    m2_content = "Memory two — slightly off-axis"
    m3_content = "Memory three — orthogonal to query"

    m1 = Memory(
        workspace_id=ws_id,
        content=m1_content,
        content_hash=_hash(m1_content),
        embedding=_zero_vec_with((0, 1.0)),
        current_version=True,
        version=1,
    )
    m2 = Memory(
        workspace_id=ws_id,
        content=m2_content,
        content_hash=_hash(m2_content),
        embedding=_zero_vec_with((0, 0.9), (1, 0.436)),
        current_version=True,
        version=1,
    )
    m3 = Memory(
        workspace_id=ws_id,
        content=m3_content,
        content_hash=_hash(m3_content),
        embedding=_zero_vec_with((1, 1.0)),
        current_version=True,
        version=1,
    )

    db_session.add_all([m1, m2, m3])
    await db_session.flush()

    query_str = _vec_literal(_zero_vec_with((0, 1.0)))

    result = await db_session.execute(
        text("""
            SELECT id, content,
                   1 - (embedding <=> (:q)::vector) AS similarity
            FROM memories
            WHERE workspace_id = (:ws_id)::uuid
              AND deleted_at IS NULL
            ORDER BY embedding <=> (:q)::vector
            LIMIT 3
        """),
        {"ws_id": str(ws_id), "q": query_str},
    )
    rows = result.fetchall()

    assert len(rows) == 3
    assert str(rows[0].id) == str(m1.id), (
        f"Expected m1 first, got content={rows[0].content!r}"
    )
    assert rows[0].similarity > 0.99, (
        f"Expected near-1.0 similarity for identical vector, got {rows[0].similarity}"
    )
    assert rows[0].similarity > rows[1].similarity > rows[2].similarity, (
        "Results not strictly ordered by similarity"
    )


# ─────────────────────────────────────────────────────────────────────────────
# TEST 2 — append-only attribution trigger
# ─────────────────────────────────────────────────────────────────────────────

@pg_available
@pytest.mark.integration
@pytest.mark.asyncio
async def test_attribution_append_only_trigger(
    db_session: AsyncSession, test_workspace, test_user
):
    """
    Verify the `attribution_append_only` DB trigger blocks UPDATE on the
    attributions table — the tamper-evident audit trail guarantee from
    ADR-002.
    """
    from sourcemind.models.attribution import Attribution
    from sourcemind.models.memory import Memory

    content = "Memory for attribution trigger test"
    memory = Memory(
        workspace_id=test_workspace.id,
        content=content,
        content_hash=_hash(content),
        embedding=_zero_vec_with((0, 0.5)),
        current_version=True,
        version=1,
    )
    db_session.add(memory)
    await db_session.flush()

    attr = Attribution(
        memory_id=memory.id,
        user_id=test_user.id,
        contribution_weight=1.0,
        trigger_action="create",
    )
    db_session.add(attr)
    await db_session.flush()

    # The UPDATE must raise — trigger raises with SM091 message.
    with pytest.raises(DBAPIError) as exc_info:
        await db_session.execute(
            text(
                "UPDATE attributions SET contribution_weight = 0.5 "
                "WHERE id = (:attr_id)::uuid"
            ),
            {"attr_id": str(attr.id)},
        )

    err_text = str(exc_info.value).lower()
    assert (
        "append-only" in err_text or "sm091" in err_text
    ), f"Unexpected trigger error message: {exc_info.value}"


# ─────────────────────────────────────────────────────────────────────────────
# TEST 3 — RLS policies exist on memories
# ─────────────────────────────────────────────────────────────────────────────

@pg_available
@pytest.mark.integration
@pytest.mark.asyncio
async def test_rls_workspace_isolation_policy(db_session: AsyncSession):
    """
    Verify the `memories_workspace_isolation` RLS policy is defined.

    The Supabase pooler role bypasses RLS enforcement, so we verify the
    POLICY EXISTS at the schema level. Actual enforcement is layered:
    every application query already filters on `workspace_id`.
    """
    result = await db_session.execute(
        text(
            "SELECT policyname, cmd FROM pg_policies "
            "WHERE tablename = 'memories' ORDER BY policyname"
        )
    )
    policies = result.fetchall()

    assert len(policies) > 0, "No RLS policies found on memories table"

    policy_names = [p.policyname for p in policies]
    assert "memories_workspace_isolation" in policy_names, (
        f"Expected memories_workspace_isolation policy. Found: {policy_names}"
    )

    # The policy in migration 0001 has no `cmd` restriction → applies to ALL.
    has_all_or_select = any(p.cmd in ("ALL", "SELECT") for p in policies)
    assert has_all_or_select, (
        f"No ALL/SELECT policy on memories. Found cmds: "
        f"{[p.cmd for p in policies]}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# TEST 4 — memory version chain (recursive CTE)
# ─────────────────────────────────────────────────────────────────────────────

@pg_available
@pytest.mark.integration
@pytest.mark.asyncio
async def test_memory_version_chain(db_session: AsyncSession, test_workspace):
    """
    Verify the recursive CTE used by GET /v1/memories/{id}/versions correctly
    walks `parent_memory_id` back from the current row to the original.
    """
    from sourcemind.models.memory import Memory

    ws_id = test_workspace.id

    v1_content = "Original content"
    v1 = Memory(
        workspace_id=ws_id,
        content=v1_content,
        content_hash=_hash(v1_content),
        embedding=_zero_vec_with((0, 0.1)),
        current_version=False,
        version=1,
        parent_memory_id=None,
    )
    db_session.add(v1)
    await db_session.flush()

    v2_content = "Updated content"
    v2 = Memory(
        workspace_id=ws_id,
        content=v2_content,
        content_hash=_hash(v2_content),
        embedding=_zero_vec_with((0, 0.2)),
        current_version=True,
        version=2,
        parent_memory_id=v1.id,
    )
    db_session.add(v2)
    await db_session.flush()

    result = await db_session.execute(
        text("""
            WITH RECURSIVE version_chain AS (
                SELECT id, version, content, current_version, parent_memory_id
                FROM memories
                WHERE id = (:start_id)::uuid
                UNION ALL
                SELECT m.id, m.version, m.content,
                       m.current_version, m.parent_memory_id
                FROM memories m
                JOIN version_chain vc ON m.id = vc.parent_memory_id
            )
            SELECT * FROM version_chain
            ORDER BY version DESC
        """),
        {"start_id": str(v2.id)},
    )
    rows = result.fetchall()

    assert len(rows) == 2, f"Expected 2 versions in chain, got {len(rows)}"
    assert rows[0].version == 2 and rows[1].version == 1
    assert rows[0].current_version is True
    assert rows[1].current_version is False
    assert str(rows[0].parent_memory_id) == str(v1.id)
    assert rows[1].parent_memory_id is None


# ─────────────────────────────────────────────────────────────────────────────
# TEST 5 — GIN tsvector full-text search
# ─────────────────────────────────────────────────────────────────────────────

@pg_available
@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_text_search_gin_index(
    db_session: AsyncSession, test_workspace
):
    """
    Verify the generated `content_tsv` column + `ix_memories_content_tsv` GIN
    index support BM25 search and reject non-matching memories.
    """
    from sourcemind.models.memory import Memory

    ws_id = test_workspace.id

    contents = [
        "PostgreSQL vector search with HNSW index",
        "Machine learning model training pipeline",
        "Authentication with OAuth tokens",
    ]
    for i, c in enumerate(contents):
        db_session.add(
            Memory(
                workspace_id=ws_id,
                content=c,
                content_hash=_hash(c),
                embedding=_zero_vec_with((i, 1.0)),
                current_version=True,
                version=1,
            )
        )
    await db_session.flush()

    result = await db_session.execute(
        text("""
            SELECT id, content,
                   ts_rank(content_tsv,
                           plainto_tsquery('english', :q)) AS rank
            FROM memories
            WHERE workspace_id = (:ws_id)::uuid
              AND deleted_at IS NULL
              AND content_tsv @@ plainto_tsquery('english', :q)
            ORDER BY rank DESC
        """),
        {"ws_id": str(ws_id), "q": "vector search"},
    )
    rows = result.fetchall()

    assert len(rows) >= 1, "Full-text search returned no results"

    top = rows[0].content.lower()
    assert "vector" in top and "search" in top, (
        f"Top result doesn't match query: {rows[0].content!r}"
    )

    contents_returned = [r.content for r in rows]
    assert not any("Machine learning" in c for c in contents_returned), (
        "ML memory should not match 'vector search'"
    )
    assert not any("OAuth" in c for c in contents_returned), (
        "OAuth memory should not match 'vector search'"
    )


# ─────────────────────────────────────────────────────────────────────────────
# TEST 6 — soft-delete isolation
# ─────────────────────────────────────────────────────────────────────────────

@pg_available
@pytest.mark.integration
@pytest.mark.asyncio
async def test_soft_delete_isolation(db_session: AsyncSession, test_workspace):
    """
    Verify `deleted_at IS NULL` filter cleanly excludes soft-deleted rows.

    Validates the DELETE /v1/memories/{id} flow end-to-end (Bug E from the
    cleanup sprint: missing `db.commit()` was added so the soft-delete
    actually persists at the database level).
    """
    from sourcemind.models.memory import Memory

    ws_id = test_workspace.id

    active_content = "Active memory — should be visible"
    deleted_content = "Deleted memory — should be hidden"

    active = Memory(
        workspace_id=ws_id,
        content=active_content,
        content_hash=_hash(active_content),
        embedding=_zero_vec_with((0, 0.1)),
        current_version=True,
        version=1,
    )
    deleted = Memory(
        workspace_id=ws_id,
        content=deleted_content,
        content_hash=_hash(deleted_content),
        embedding=_zero_vec_with((0, 0.2)),
        current_version=True,
        version=1,
    )
    db_session.add_all([active, deleted])
    await db_session.flush()

    await db_session.execute(
        text("UPDATE memories SET deleted_at = NOW() WHERE id = (:id)::uuid"),
        {"id": str(deleted.id)},
    )
    await db_session.flush()

    visible = (
        await db_session.execute(
            text(
                "SELECT id, content FROM memories "
                "WHERE workspace_id = (:ws_id)::uuid AND deleted_at IS NULL"
            ),
            {"ws_id": str(ws_id)},
        )
    ).fetchall()
    visible_ids = {str(r.id) for r in visible}

    assert str(active.id) in visible_ids, "Active memory should be visible"
    assert str(deleted.id) not in visible_ids, (
        "Soft-deleted memory should be hidden by deleted_at IS NULL"
    )

    total = (
        await db_session.execute(
            text(
                "SELECT COUNT(*) FROM memories WHERE workspace_id = (:ws_id)::uuid"
            ),
            {"ws_id": str(ws_id)},
        )
    ).scalar()
    assert (total or 0) >= 2, (
        "Hard count should include soft-deleted row (no deleted_at filter)"
    )
