"""Unit tests for memory versioning service."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_memory(
    memory_id: uuid.UUID | None = None,
    version: int = 1,
    content: str = "Original content.",
    parent_memory_id: uuid.UUID | None = None,
) -> MagicMock:
    m = MagicMock()
    m.id = memory_id or uuid.uuid4()
    m.workspace_id = uuid.UUID("00000000-0000-4000-8000-000000000010")
    m.document_id = None
    m.parent_memory_id = parent_memory_id
    m.content = content
    m.embedding = [0.1] * 3072
    m.version = version
    m.current_version = True
    m.tags = None
    m.category = None
    m.source_chunk_index = None
    m.confidence_score = None
    return m


@pytest.mark.unit
@pytest.mark.asyncio
async def test_patch_creates_new_version_with_incremented_number():
    """PATCH should create a new Memory with version = old.version + 1."""
    from sourcemind.services.attribution.versioning import create_new_version

    old_id = uuid.uuid4()
    old_mem = _make_memory(memory_id=old_id, version=1, content="Original.")

    mock_session = AsyncMock()
    scalar_result = MagicMock()
    scalar_result.scalar_one_or_none = MagicMock(return_value=old_mem)
    mock_session.execute = AsyncMock(return_value=scalar_result)

    added_objects = []
    mock_session.add = MagicMock(side_effect=added_objects.append)

    async def flush_side_effect():
        if added_objects:
            added_objects[-1].id = uuid.uuid4()

    mock_session.flush = AsyncMock(side_effect=flush_side_effect)

    result = await create_new_version(
        session=mock_session,
        memory_id=old_id,
        new_content="Updated content.",
        new_tags=None,
        openai_client=None,
    )

    assert result.new_memory.version == 2
    assert result.new_memory.current_version is True
    assert result.new_memory.content == "Updated content."


@pytest.mark.unit
@pytest.mark.asyncio
async def test_old_version_is_latest_set_false():
    """create_new_version must issue an UPDATE to set current_version=FALSE on old."""
    from sourcemind.services.attribution.versioning import create_new_version

    old_id = uuid.uuid4()
    old_mem = _make_memory(memory_id=old_id, version=2)

    mock_session = AsyncMock()

    call_count = [0]
    update_called = [False]

    async def execute_side_effect(stmt, *args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            # First call: SELECT (return old_mem)
            r = MagicMock()
            r.scalar_one_or_none = MagicMock(return_value=old_mem)
            return r
        else:
            # Subsequent calls: UPDATE and INSERT copies
            if "current_version = FALSE" in str(stmt):
                update_called[0] = True
            r = MagicMock()
            r.fetchall = MagicMock(return_value=[])
            return r

    mock_session.execute = AsyncMock(side_effect=execute_side_effect)
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()

    await create_new_version(
        session=mock_session,
        memory_id=old_id,
        new_content="New content.",
        new_tags=None,
        openai_client=None,
    )

    assert update_called[0], "UPDATE current_version=FALSE was not called"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_parent_id_links_to_previous_version():
    """New memory's parent_memory_id must equal the old memory's id."""
    from sourcemind.services.attribution.versioning import create_new_version

    old_id = uuid.uuid4()
    old_mem = _make_memory(memory_id=old_id, version=1)

    mock_session = AsyncMock()
    call_count = [0]

    async def execute_side_effect(stmt, *args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            r = MagicMock()
            r.scalar_one_or_none = MagicMock(return_value=old_mem)
            return r
        r = MagicMock()
        r.fetchall = MagicMock(return_value=[])
        return r

    mock_session.execute = AsyncMock(side_effect=execute_side_effect)
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()

    result = await create_new_version(
        session=mock_session,
        memory_id=old_id,
        new_content="Updated.",
        new_tags=None,
    )

    assert result.new_memory.parent_memory_id == old_id
    assert result.previous_version_id == old_id


@pytest.mark.unit
@pytest.mark.asyncio
async def test_version_chain_query_returns_all_versions_in_order():
    """get_version_chain must return versions sorted by version DESC."""
    from sourcemind.services.attribution.versioning import get_version_chain

    mid = uuid.uuid4()
    mock_session = AsyncMock()
    rows = [
        (str(uuid.uuid4()), 3, True, "v3 content", "2025-03-12"),
        (str(uuid.uuid4()), 2, False, "v2 content", "2025-03-11"),
        (str(mid), 1, False, "v1 content", "2025-03-10"),
    ]
    result_mock = MagicMock()
    result_mock.fetchall = MagicMock(return_value=rows)
    mock_session.execute = AsyncMock(return_value=result_mock)

    chain = await get_version_chain(mock_session, mid)

    assert len(chain) == 3
    assert chain[0]["version"] == 3
    assert chain[0]["is_current"] is True
    assert chain[2]["version"] == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_embedding_recomputed_when_content_changes():
    """When content changes and openai_client is provided, embedding_recomputed=True."""
    from sourcemind.services.attribution.versioning import create_new_version
    from sourcemind.services.ingestion.embedder import EmbeddingResult

    old_id = uuid.uuid4()
    old_mem = _make_memory(memory_id=old_id, version=1, content="Original.")

    mock_session = AsyncMock()
    call_count = [0]

    async def execute_side_effect(stmt, *args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            r = MagicMock()
            r.scalar_one_or_none = MagicMock(return_value=old_mem)
            return r
        r = MagicMock()
        r.fetchall = MagicMock(return_value=[])
        return r

    mock_session.execute = AsyncMock(side_effect=execute_side_effect)
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()

    mock_openai = AsyncMock()
    fake_embedding = [0.2] * 3072
    fake_result = EmbeddingResult(
        content="Updated content.", embedding=fake_embedding, token_count=0, cached=False
    )

    with patch("sourcemind.services.attribution.versioning.EmbeddingService") as mock_cls:
        mock_svc = AsyncMock()
        mock_svc.embed = AsyncMock(return_value=[fake_result])
        mock_cls.return_value = mock_svc

        result = await create_new_version(
            session=mock_session,
            memory_id=old_id,
            new_content="Updated content.",
            new_tags=None,
            openai_client=mock_openai,
        )

    assert result.embedding_recomputed is True
    assert result.new_memory.embedding == fake_embedding
