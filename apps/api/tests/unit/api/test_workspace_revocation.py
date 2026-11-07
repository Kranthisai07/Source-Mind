"""Focused regression coverage for membership revocation side effects."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm.exc import StaleDataError

from sourcemind.api.v1 import workspaces
from sourcemind.core.dependencies import AuthenticatedUser
from sourcemind.core.exceptions import ServiceUnavailableError, WorkspaceAccessDeniedError
from sourcemind.models.document import IngestionStatus


@pytest.mark.unit
@pytest.mark.asyncio
async def test_revocation_fails_pending_ingestion_in_same_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    admin_id = uuid.uuid4()
    revoked_user_id = uuid.uuid4()

    membership = MagicMock(role="member", status="active", departed_at=None)
    membership_result = MagicMock()
    membership_result.scalar_one_or_none.return_value = membership

    pending_document = MagicMock(
        ingestion_status=IngestionStatus.PENDING.value,
        error_message=None,
        pipeline_data={"current_stage": "queued", "raw_content": "unprocessed"},
    )
    documents_result = MagicMock()
    documents_result.scalars.return_value.all.return_value = [pending_document]

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[membership_result, documents_result])
    session.commit = AsyncMock()
    monkeypatch.setattr(
        workspaces,
        "require_workspace_permission",
        AsyncMock(return_value="admin"),
    )
    record = AsyncMock()
    monkeypatch.setattr(workspaces.audit, "record", record)

    await workspaces.revoke_workspace_member(
        workspace_id=workspace_id,
        user_id=revoked_user_id,
        db=session,
        current_user=AuthenticatedUser(
            user_id=admin_id,
            clerk_id="admin_test",
            email="admin@example.com",
        ),
        idempotency_key=str(uuid.uuid4()),
    )

    assert pending_document.ingestion_status == IngestionStatus.FAILED.value
    assert pending_document.error_message == "Workspace access revoked before ingestion."
    assert pending_document.pipeline_data == {
        "current_stage": "failed",
        "raw_content": "unprocessed",
    }
    assert membership.status == "departed"
    # The audit event is written before the status change, in the same transaction.
    record.assert_awaited_once()
    assert record.await_args.kwargs["event"] == "member_removed"
    assert record.await_args.kwargs["target_user_id"] == revoked_user_id
    session.commit.assert_awaited_once()


class _PgError(Exception):
    def __init__(self, message: str, sqlstate: str | None = None) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


async def _revoke_failing_at_commit(monkeypatch: pytest.MonkeyPatch, failure: Exception):
    """Run the revoke route with a session whose commit fails with ``failure``."""
    membership = MagicMock(role="member", status="active", departed_at=None)
    membership_result = MagicMock()
    membership_result.scalar_one_or_none.return_value = membership
    documents_result = MagicMock()
    documents_result.scalars.return_value.all.return_value = []

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[membership_result, documents_result])
    session.commit = AsyncMock(side_effect=failure)
    session.rollback = AsyncMock()
    monkeypatch.setattr(workspaces, "require_workspace_permission", AsyncMock(return_value="admin"))
    monkeypatch.setattr(workspaces.audit, "record", AsyncMock())

    caller_id = uuid.uuid4()
    try:
        await workspaces.revoke_workspace_member(
            workspace_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            db=session,
            current_user=AuthenticatedUser(
                user_id=caller_id, clerk_id="admin_test", email="admin@example.com"
            ),
            idempotency_key=str(uuid.uuid4()),
        )
    except Exception as exc:  # the caller inspects what came out
        return exc, session
    return None, session


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_revocation_that_loses_the_race_for_its_rows_is_a_retryable_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-081: the winner committed first, so this UPDATE matched no rows (StaleDataError)."""
    stale = StaleDataError(
        "UPDATE statement on table 'workspace_members' expected to update 1 row(s); 0 were matched."
    )
    outcome, session = await _revoke_failing_at_commit(monkeypatch, stale)

    assert isinstance(outcome, ServiceUnavailableError)
    assert outcome.details == {"reason": "concurrent_update_retry"}
    session.rollback.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_revocation_stopped_by_the_last_owner_trigger_is_a_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = DBAPIError(
        "UPDATE workspace_members",
        {},
        _PgError("last active workspace owner cannot be removed"),
    )
    outcome, session = await _revoke_failing_at_commit(monkeypatch, failure)

    assert isinstance(outcome, WorkspaceAccessDeniedError)
    assert "last active workspace owner" in outcome.message
    session.rollback.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("sqlstate", ["40001", "40P01"])
async def test_a_revocation_the_database_asks_to_retry_is_a_503(
    monkeypatch: pytest.MonkeyPatch, sqlstate: str
) -> None:
    failure = DBAPIError("UPDATE workspace_members", {}, _PgError("conflict", sqlstate))
    outcome, _session = await _revoke_failing_at_commit(monkeypatch, failure)

    assert isinstance(outcome, ServiceUnavailableError)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_an_unrelated_database_error_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = DBAPIError("UPDATE workspace_members", {}, _PgError("disk full", "53100"))
    outcome, session = await _revoke_failing_at_commit(monkeypatch, failure)

    assert outcome is failure
    session.rollback.assert_awaited_once()
