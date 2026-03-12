"""Unit tests for Stage 1: Receiver."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mock_workspace(ws_id: uuid.UUID) -> MagicMock:
    ws = MagicMock()
    ws.id = ws_id
    return ws


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_content_and_url_raises_validation_error():
    from sourcemind.core.exceptions import ValidationError
    from sourcemind.services.ingestion.receiver import receive

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=_mock_workspace(uuid.uuid4())))
    )

    with pytest.raises(ValidationError, match="Either"):
        await receive(
            session=mock_session,
            workspace_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            content=None,
            url=None,
            idempotency_key=str(uuid.uuid4()),
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_content_too_large_raises_error():
    from sourcemind.core.exceptions import ContentTooLargeError
    from sourcemind.services.ingestion.receiver import _MAX_CONTENT_CHARS, receive

    huge_content = "a" * (_MAX_CONTENT_CHARS + 1)

    mock_session = AsyncMock()

    with pytest.raises(ContentTooLargeError):
        await receive(
            session=mock_session,
            workspace_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            content=huge_content,
            idempotency_key=str(uuid.uuid4()),
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_duplicate_content_returns_existing():
    """SHA-256 match returns existing document without enqueuing new task."""
    from sourcemind.services.ingestion.receiver import receive
    from sourcemind.models.document import IngestionStatus

    existing_doc_id = uuid.uuid4()
    existing_doc = MagicMock()
    existing_doc.id = existing_doc_id
    existing_doc.ingestion_job_id = "existing-job-123"
    existing_doc.ingestion_status = IngestionStatus.COMPLETED
    existing_doc.memory_count = 5
    existing_doc.deleted_at = None

    mock_session = AsyncMock()
    ws_id = uuid.uuid4()

    # Mock workspace check → found; duplicate check → found
    def execute_side_effect(stmt, *args, **kwargs):
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=None)
        return result

    # First call = workspace check, second call = duplicate check
    call_count = {"n": 0}

    def smart_execute(stmt, *args, **kwargs):
        result = MagicMock()
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Workspace check
            result.scalar_one_or_none = MagicMock(return_value=_mock_workspace(ws_id))
        else:
            # Duplicate check
            result.scalar_one_or_none = MagicMock(return_value=existing_doc)
        return result

    mock_session.execute = AsyncMock(side_effect=smart_execute)

    with patch("sourcemind.services.ingestion.receiver.get_redis") as mock_redis:
        mock_redis.return_value.get = AsyncMock(return_value=None)
        mock_redis.return_value.setex = AsyncMock()

        result = await receive(
            session=mock_session,
            workspace_id=ws_id,
            user_id=uuid.uuid4(),
            content="Duplicate content.",
            idempotency_key=str(uuid.uuid4()),
        )

    assert result["already_exists"] is True
    assert result["document_id"] == str(existing_doc_id)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workspace_not_found_raises_error():
    from sourcemind.core.exceptions import WorkspaceNotFoundError
    from sourcemind.services.ingestion.receiver import receive

    mock_session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=None)
    mock_session.execute = AsyncMock(return_value=result)

    with pytest.raises(WorkspaceNotFoundError):
        await receive(
            session=mock_session,
            workspace_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            content="Some content.",
            idempotency_key=str(uuid.uuid4()),
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_invalid_url_raises_validation_error():
    from sourcemind.core.exceptions import ValidationError
    from sourcemind.services.ingestion.receiver import receive

    mock_session = AsyncMock()

    with pytest.raises(ValidationError, match="http"):
        await receive(
            session=mock_session,
            workspace_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            url="ftp://not-valid.com",
            idempotency_key=str(uuid.uuid4()),
        )
