"""Unit tests for GitHubConnector orchestrator."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_config(repos=None):
    """Create a mock ConnectorConfig."""
    config = MagicMock()
    config.id = uuid.uuid4()
    config.workspace_id = uuid.uuid4()
    config.connector_type = "github"
    config.display_name = "Test Connector"
    config.config = {"repos": repos or ["acme/repo"]}
    config.status = "active"
    config.last_sync_at = None
    return config


@pytest.fixture
def mock_session():
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.execute = AsyncMock()
    # Dedup check: return no existing ArtifactLink
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    session.execute.return_value = mock_result
    return session


@pytest.fixture
def mock_auth():
    auth = AsyncMock()
    auth.get_token = AsyncMock(return_value="test_token")
    auth.invalidate_cache = AsyncMock()
    return auth


@pytest.mark.unit
@pytest.mark.anyio
async def test_sync_creates_sync_log(mock_session, mock_auth):
    """sync() should create a ConnectorSyncLog and flush it."""
    from sourcemind.connectors.github.connector import GitHubConnector

    config = _make_config(repos=["acme/repo"])
    workspace_id = uuid.uuid4()
    user_id = uuid.uuid4()

    connector = GitHubConnector(
        config=config,
        auth=mock_auth,
        session=mock_session,
        workspace_id=workspace_id,
        user_id=user_id,
    )

    # Mock the client so no HTTP calls are made
    connector._client = AsyncMock()
    # Return empty iterators for all fetch methods
    async def _empty_aiter(*args, **kwargs):
        return
        yield  # make it an async generator

    connector._client.get_commits = _empty_aiter
    connector._client.get_pull_requests = _empty_aiter
    connector._client.get_issues = _empty_aiter
    connector._client.get_discussions = AsyncMock(return_value=[])

    sync_log = await connector.sync(sync_type="full")

    # A sync log should have been added to the session
    assert mock_session.add.called
    assert sync_log.status in ("completed", "failed")


@pytest.mark.unit
@pytest.mark.anyio
async def test_ingest_skips_duplicate(mock_session, mock_auth):
    """_ingest() should return False if ArtifactLink already exists."""
    from sourcemind.connectors.github.connector import GitHubConnector
    from sourcemind.connectors.github.mapper import ConnectorDocument

    config = _make_config()
    connector = GitHubConnector(
        config=config,
        auth=mock_auth,
        session=mock_session,
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    # Simulate existing ArtifactLink
    existing = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing
    mock_session.execute.return_value = mock_result

    doc = ConnectorDocument(
        source_tool="github",
        source_type="commit",
        source_id="acme/repo/commits/abc123",
        title="test",
        content="test content",
        idempotency_key=ConnectorDocument.make_key("github", "commit", "acme/repo/commits/abc123"),
    )
    result = await connector._ingest(doc)
    assert result is False


@pytest.mark.unit
@pytest.mark.anyio
async def test_ingest_new_artifact_returns_true(mock_session, mock_auth):
    """_ingest() should return True for a new artifact."""
    from sourcemind.connectors.github.connector import GitHubConnector
    from sourcemind.connectors.github.mapper import ConnectorDocument

    config = _make_config()
    workspace_id = uuid.uuid4()
    connector = GitHubConnector(
        config=config,
        auth=mock_auth,
        session=mock_session,
        workspace_id=workspace_id,
        user_id=uuid.uuid4(),
    )

    # No existing ArtifactLink
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result

    doc = ConnectorDocument(
        source_tool="github",
        source_type="commit",
        source_id="acme/repo/commits/new123",
        title="New commit",
        content="new content",
        idempotency_key=ConnectorDocument.make_key("github", "commit", "acme/repo/commits/new123"),
    )

    # Mock the ingestion receiver
    with patch(
        "sourcemind.connectors.github.connector.receive",
        new=AsyncMock(return_value={
            "already_exists": False,
            "document_id": str(uuid.uuid4()),
        }),
    ):
        result = await connector._ingest(doc)

    assert result is True
