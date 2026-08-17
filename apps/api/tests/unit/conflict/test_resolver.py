"""Unit tests for conflict resolution service."""

import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_session(conflict_row=None, mem_row=None):
    """Create a mock async session."""
    mock_session = AsyncMock()
    call_count = [0]

    def make_result(row):
        r = MagicMock()
        r.fetchone = MagicMock(return_value=row)
        r.fetchall = MagicMock(return_value=[row] if row else [])
        return r

    async def execute_side_effect(stmt, *args, **kwargs):
        call_count[0] += 1
        stmt_str = str(stmt)
        if call_count[0] == 1 and mem_row:
            return make_result(mem_row)
        return make_result(None)

    mock_session.execute = AsyncMock(side_effect=execute_side_effect)
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()
    return mock_session


@pytest.mark.unit
@pytest.mark.asyncio
async def test_kept_a_marks_memory_b_not_latest():
    """Resolving with kept_a should UPDATE memories SET current_version=FALSE for memory_b."""
    from sourcemind.services.conflict.resolver import resolve_conflict

    mem_a_id = str(uuid.uuid4())
    mem_b_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    conflict_id = uuid.uuid4()
    resolver_id = uuid.uuid4()

    calls_made = []
    call_count = [0]

    async def execute_side_effect(stmt, params=None, **kwargs):
        call_count[0] += 1
        stmt_str = str(stmt)
        calls_made.append(stmt_str)

        r = MagicMock()
        if "SELECT memory_a_id" in stmt_str:
            r.fetchone = MagicMock(return_value=(mem_a_id, mem_b_id, ws_id))
        else:
            r.fetchone = MagicMock(return_value=None)
        return r

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=execute_side_effect)
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()

    result = await resolve_conflict(
        session=mock_session,
        conflict_id=conflict_id,
        resolver_id=resolver_id,
        resolution_type="kept_a",
        resolution_note="Memory A is more accurate.",
    )

    assert result is True
    update_calls = [c for c in calls_made if "current_version = FALSE" in c]
    assert len(update_calls) >= 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_kept_b_marks_memory_a_not_latest():
    """Resolving with kept_b should UPDATE memories SET current_version=FALSE for memory_a."""
    from sourcemind.services.conflict.resolver import resolve_conflict

    mem_a_id = str(uuid.uuid4())
    mem_b_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    conflict_id = uuid.uuid4()

    calls_made = []

    async def execute_side_effect(stmt, params=None, **kwargs):
        stmt_str = str(stmt)
        calls_made.append(stmt_str)
        r = MagicMock()
        if "SELECT memory_a_id" in stmt_str:
            r.fetchone = MagicMock(return_value=(mem_a_id, mem_b_id, ws_id))
        else:
            r.fetchone = MagicMock(return_value=None)
        return r

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=execute_side_effect)
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()

    result = await resolve_conflict(
        session=mock_session,
        conflict_id=conflict_id,
        resolver_id=uuid.uuid4(),
        resolution_type="kept_b",
    )

    assert result is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_deferred_sets_revisit_date():
    """Deferred resolution should store revisit_at and NOT update status to resolved."""
    from sourcemind.services.conflict.resolver import resolve_conflict

    mem_a_id = str(uuid.uuid4())
    mem_b_id = str(uuid.uuid4())
    revisit_date = datetime.now(timezone.utc) + timedelta(days=30)
    conflict_id = uuid.uuid4()

    deferred_params = {}

    async def execute_side_effect(stmt, params=None, **kwargs):
        stmt_str = str(stmt)
        r = MagicMock()
        if "SELECT memory_a_id" in stmt_str:
            r.fetchone = MagicMock(return_value=(mem_a_id, mem_b_id, str(uuid.uuid4())))
        elif "deferred" in stmt_str and params:
            deferred_params.update(params or {})
            r.fetchone = MagicMock(return_value=(str(conflict_id),))
        else:
            r.fetchone = MagicMock(return_value=None)
        return r

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=execute_side_effect)

    result = await resolve_conflict(
        session=mock_session,
        conflict_id=conflict_id,
        resolver_id=uuid.uuid4(),
        resolution_type="deferred",
        revisit_at=revisit_date,
    )

    assert result is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_split_adds_tags_to_both_memories():
    """Split resolution should add tag_a to memory_a and tag_b to memory_b."""
    from sourcemind.services.conflict.resolver import resolve_conflict

    mem_a_id = str(uuid.uuid4())
    mem_b_id = str(uuid.uuid4())
    tag_update_params = []

    async def execute_side_effect(stmt, params=None, **kwargs):
        stmt_str = str(stmt)
        r = MagicMock()
        if "SELECT memory_a_id" in stmt_str:
            r.fetchone = MagicMock(return_value=(mem_a_id, mem_b_id, str(uuid.uuid4())))
        elif "array_append" in stmt_str and params:
            tag_update_params.append(dict(params))
            r.fetchone = MagicMock(return_value=None)
        else:
            r.fetchone = MagicMock(return_value=None)
        return r

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=execute_side_effect)
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()

    result = await resolve_conflict(
        session=mock_session,
        conflict_id=uuid.uuid4(),
        resolver_id=uuid.uuid4(),
        resolution_type="split",
        tag_a="us-east",
        tag_b="eu-west",
    )

    assert result is True
    assert len(tag_update_params) == 2
    tags_applied = {p["tag"] for p in tag_update_params}
    assert "us-east" in tags_applied
    assert "eu-west" in tags_applied


@pytest.mark.unit
@pytest.mark.asyncio
async def test_merged_deprecates_both_creates_new():
    """Merged resolution should deprecate both memories and INSERT a new merged memory."""
    from sourcemind.services.conflict.resolver import resolve_conflict

    mem_a_id = str(uuid.uuid4())
    mem_b_id = str(uuid.uuid4())
    ws_id = str(uuid.UUID("00000000-0000-4000-8000-000000000010"))
    added_objects = []

    async def execute_side_effect(stmt, params=None, **kwargs):
        stmt_str = str(stmt)
        r = MagicMock()
        if "SELECT memory_a_id" in stmt_str:
            r.fetchone = MagicMock(return_value=(mem_a_id, mem_b_id, ws_id))
        else:
            r.fetchone = MagicMock(return_value=None)
        return r

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=execute_side_effect)
    mock_session.add = MagicMock(side_effect=added_objects.append)
    mock_session.flush = AsyncMock()

    result = await resolve_conflict(
        session=mock_session,
        conflict_id=uuid.uuid4(),
        resolver_id=uuid.uuid4(),
        resolution_type="merged",
        merged_content="The system deployed to both AWS us-east-1 and eu-west-1.",
    )

    assert result is True
    # A new merged memory should have been added to the session
    from sourcemind.models.memory import Memory
    memory_objects = [o for o in added_objects if isinstance(o, Memory)]
    assert len(memory_objects) == 1
    assert memory_objects[0].content == "The system deployed to both AWS us-east-1 and eu-west-1."


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_requires_known_type():
    """Unknown resolution_type should raise ValueError."""
    from sourcemind.services.conflict.resolver import resolve_conflict

    mem_a_id = str(uuid.uuid4())
    mem_b_id = str(uuid.uuid4())

    async def execute_side_effect(stmt, params=None, **kwargs):
        r = MagicMock()
        r.fetchone = MagicMock(return_value=(mem_a_id, mem_b_id, str(uuid.uuid4())))
        return r

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=execute_side_effect)

    with pytest.raises(ValueError, match="Unknown resolution_type"):
        await resolve_conflict(
            session=mock_session,
            conflict_id=uuid.uuid4(),
            resolver_id=uuid.uuid4(),
            resolution_type="invalid_type",
        )


# ─────────────────────────────────────────────────────────────────────────────
# GAP 1 — merged resolution must produce a complete, traceable memory
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_merged_memory_is_embedded_attributed_and_linked():
    """A merged memory must be a first-class memory, not an orphan.

    It previously got no embedding (invisible to semantic search), no
    attribution (no provenance), and no link to the two memories it merged,
    under a comment claiming a separate process handled attribution. Nothing
    did. This asserts all four properties the fix guarantees.
    """
    from sourcemind.models.memory import Memory
    from sourcemind.services.conflict.resolver import resolve_conflict

    mem_a_id = str(uuid.uuid4())
    mem_b_id = str(uuid.uuid4())
    ws_id = str(uuid.UUID("00000000-0000-4000-8000-000000000010"))
    user_a, user_b = str(uuid.uuid4()), str(uuid.uuid4())

    added: list = []
    statements: list[tuple[str, dict]] = []
    primary_calls = {"n": 0}

    async def execute_side_effect(stmt, params=None, **kwargs):
        s = str(stmt)
        statements.append((s, params or {}))
        r = MagicMock()
        if "SELECT memory_a_id" in s:
            r.fetchone = MagicMock(return_value=(mem_a_id, mem_b_id, ws_id))
        elif "DISTINCT ON (user_id)" in s:
            # Primary contributor lookup: memory A -> user_a, memory B -> user_b
            primary_calls["n"] += 1
            row = MagicMock()
            row.user_id = uuid.UUID(user_a if primary_calls["n"] == 1 else user_b)
            r.first = MagicMock(return_value=row)
        else:
            r.fetchone = MagicMock(return_value=None)
            r.first = MagicMock(return_value=None)
        return r

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=execute_side_effect)
    session.add = MagicMock(side_effect=added.append)
    session.flush = AsyncMock()

    # Minimal OpenAI stub: the embedder calls client.embeddings.create(...)
    vector = [0.05] * 3072
    fake_client = MagicMock()
    fake_client.embeddings = MagicMock()
    fake_client.embeddings.create = AsyncMock(
        return_value=MagicMock(data=[MagicMock(embedding=vector)])
    )

    # The embedder caches vectors in Redis. Simulate a cold cache so it falls
    # through to the (stubbed) API call rather than erroring.
    fake_redis = AsyncMock()
    fake_redis.get = AsyncMock(return_value=None)
    fake_redis.setex = AsyncMock()

    with patch(
        "sourcemind.services.ingestion.embedder.get_redis", return_value=fake_redis
    ):
        ok = await resolve_conflict(
            session=session,
            conflict_id=uuid.uuid4(),
            resolver_id=uuid.uuid4(),
            resolution_type="merged",
            merged_content="The service runs in both us-east-1 and eu-west-1.",
            openai_client=fake_client,
        )

    assert ok is True

    # 1. The merged memory exists and carries a real embedding
    memories = [o for o in added if isinstance(o, Memory)]
    assert len(memories) == 1, "exactly one merged memory should be created"
    merged = memories[0]
    assert merged.embedding is not None, (
        "merged memory has no embedding — it would be invisible to semantic search"
    )
    assert len(merged.embedding) == 3072
    assert merged.current_version is True

    # 2. Attribution split 50/50 between both sources' primary contributors
    attribution_stmts = [(s, p) for s, p in statements if "INSERT INTO attributions" in s]
    assert len(attribution_stmts) == 2, (
        f"expected one attribution row per contributor, got {len(attribution_stmts)}"
    )
    assert all(p["w"] == 0.5 for _, p in attribution_stmts), "weights must be 50/50"
    assert {p["uid"] for _, p in attribution_stmts} == {user_a, user_b}
    assert all("'merged'" in s for s, _ in attribution_stmts)

    # 3. Provenance: a `derives` relation from each source to the merged memory
    relation_stmts = [(s, p) for s, p in statements if "INSERT INTO memory_relations" in s]
    assert len(relation_stmts) == 2, "expected a derives relation to each source"
    assert all("'derives'" in s for s, _ in relation_stmts)
    assert {p["src"] for _, p in relation_stmts} == {mem_a_id, mem_b_id}

    # 4. Both sources superseded via the existing versioning flag
    supersede = [
        (s, p) for s, p in statements
        if "UPDATE memories SET current_version = FALSE" in s
    ]
    assert supersede, "source memories were not superseded"
    params = supersede[-1][1]
    assert {params["a"], params["b"]} == {mem_a_id, mem_b_id}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_merged_shared_contributor_gets_full_weight_once():
    """When both sources share a primary contributor, weights still sum to 1.0.

    Two 0.5 rows for the same person would be wrong: they authored the whole
    thing, and duplicate rows would distort every downstream weight query.
    """
    from sourcemind.services.conflict.resolver import resolve_conflict

    mem_a_id, mem_b_id = str(uuid.uuid4()), str(uuid.uuid4())
    ws_id = str(uuid.UUID("00000000-0000-4000-8000-000000000010"))
    same_user = uuid.uuid4()
    statements: list[tuple[str, dict]] = []

    async def execute_side_effect(stmt, params=None, **kwargs):
        s = str(stmt)
        statements.append((s, params or {}))
        r = MagicMock()
        if "SELECT memory_a_id" in s:
            r.fetchone = MagicMock(return_value=(mem_a_id, mem_b_id, ws_id))
        elif "DISTINCT ON (user_id)" in s:
            row = MagicMock()
            row.user_id = same_user
            r.first = MagicMock(return_value=row)
        else:
            r.fetchone = MagicMock(return_value=None)
            r.first = MagicMock(return_value=None)
        return r

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=execute_side_effect)
    session.add = MagicMock()
    session.flush = AsyncMock()

    await resolve_conflict(
        session=session,
        conflict_id=uuid.uuid4(),
        resolver_id=uuid.uuid4(),
        resolution_type="merged",
        merged_content="Single-author merge.",
        openai_client=None,  # no client: embedding skipped, must not crash
    )

    rows = [(s, p) for s, p in statements if "INSERT INTO attributions" in s]
    assert len(rows) == 1, "a shared contributor should get one row, not two"
    assert rows[0][1]["w"] == 1.0
    assert rows[0][1]["uid"] == str(same_user)
