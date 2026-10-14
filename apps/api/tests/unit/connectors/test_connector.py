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
    mock_result.all.return_value = []  # dedup reads link state with .all()
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
async def test_run_exits_skipped_locked_when_the_lease_is_held(mock_session, mock_auth):
    """T-L5 (unit half): a trigger that cannot take the lease does nothing at all."""
    from sourcemind.connectors.github.connector import GitHubConnector

    connector = GitHubConnector(
        config=_make_config(),
        auth=mock_auth,
        session=mock_session,
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        client=AsyncMock(),
    )
    with patch(
        "sourcemind.services.connectors.sync_state.acquire_lease",
        new=AsyncMock(return_value=None),
    ):
        outcome = await connector.sync(sync_type="full")

    assert outcome.status == "skipped_locked"
    mock_session.add.assert_not_called()  # no sync log, no write
    connector._client.assert_not_called()


@pytest.mark.unit
@pytest.mark.anyio
async def test_lease_loss_mid_run_stops_with_lease_lost_status(mock_session, mock_auth):
    from sourcemind.connectors.github.connector import GitHubConnector
    from sourcemind.services.connectors.sync_state import LeaseLostError, LeaseToken

    config = _make_config()
    connector = GitHubConnector(
        config=config,
        auth=mock_auth,
        session=mock_session,
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        client=AsyncMock(),
    )
    token = LeaseToken(config.id, 1, "t")
    with (
        patch(
            "sourcemind.services.connectors.sync_state.acquire_lease",
            new=AsyncMock(return_value=token),
        ),
        patch(
            "sourcemind.services.connectors.sync_state.renew_lease",
            new=AsyncMock(side_effect=LeaseLostError("fenced")),
        ),
    ):
        outcome = await connector.sync()

    assert outcome.status == "lease_lost"
    mock_session.add.assert_not_called()


@pytest.mark.unit
@pytest.mark.anyio
async def test_ingest_skips_a_revision_the_receiver_already_has(mock_session, mock_auth):
    """_ingest() returns False when the receiver reports the same source revision."""
    from unittest.mock import AsyncMock, patch

    from sourcemind.connectors.github.connector import GitHubConnector
    from sourcemind.connectors.github.mapper import ConnectorDocument

    connector = GitHubConnector(
        config=_make_config(),
        auth=mock_auth,
        session=mock_session,
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )
    doc = ConnectorDocument(
        source_tool="github",
        source_type="commit",
        source_id="acme/repo/commits/abc123",
        title="test",
        content="test content",
        idempotency_key=ConnectorDocument.make_key("github", "commit", "acme/repo/commits/abc123"),
    )
    receive = AsyncMock(return_value={"already_exists": True, "reason": "same_revision"})
    with patch("sourcemind.connectors.github.connector.receive", new=receive):
        assert await connector._ingest(doc) is False


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
    mock_result.all.return_value = []
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
