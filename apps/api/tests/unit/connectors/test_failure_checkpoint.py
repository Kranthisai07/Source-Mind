"""Regression for failed GitHub artifacts advancing the incremental checkpoint."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _commit(sha: str, message: str) -> dict[str, object]:
    return {
        "sha": sha,
        "html_url": f"https://github.com/acme/repo/commit/{sha}",
        "commit": {
            "message": message,
            "author": {"name": "Reviewer", "date": "2026-09-21T12:00:00Z"},
        },
        "author": {"login": "reviewer"},
    }


@pytest.mark.unit
@pytest.mark.anyio
async def test_failed_artifact_preserves_checkpoint_and_is_retried() -> None:
    from sourcemind.connectors.github.connector import GitHubConnector

    original_checkpoint = datetime(2026, 9, 20, tzinfo=UTC)
    config = MagicMock()
    config.id = uuid.uuid4()
    config.config = {"repos": ["acme/repo"]}
    config.last_sync_at = original_checkpoint

    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    duplicate_result = MagicMock()
    duplicate_result.scalar_one_or_none.return_value = MagicMock()
    new_result = MagicMock()
    new_result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(
        side_effect=[new_result, new_result, duplicate_result, new_result]
    )

    connector = GitHubConnector(
        config=config,
        auth=AsyncMock(),
        session=session,
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )
    commits = [
        _commit("a" * 40, "already persisted before the later failure"),
        _commit("b" * 40, "fails once and must be retried"),
    ]
    seen_since: list[str | None] = []

    async def get_commits(
        owner: str,
        repo: str,
        since: str | None = None,
    ) -> object:
        seen_since.append(since)
        for commit in commits:
            yield commit

    async def empty_iterator(*args: object, **kwargs: object) -> object:
        return
        yield

    connector._client.get_commits = get_commits
    connector._client.get_pull_requests = empty_iterator
    connector._client.get_issues = empty_iterator
    connector._client.get_discussions = AsyncMock(return_value=[])

    receive_result = {
        "already_exists": False,
        "document_id": str(uuid.uuid4()),
    }
    receiver = AsyncMock(
        side_effect=[receive_result, RuntimeError("transient ingest failure"), receive_result]
    )
    with patch("sourcemind.connectors.github.connector.receive", new=receiver):
        first = await connector.sync(sync_type="incremental")
        assert first.status == "failed"
        assert config.last_sync_at == original_checkpoint

        second = await connector.sync(sync_type="incremental")

    assert second.status == "completed"
    assert config.last_sync_at > original_checkpoint
    assert seen_since == [
        original_checkpoint.isoformat(),
        original_checkpoint.isoformat(),
    ]
    assert receiver.await_count == 3
