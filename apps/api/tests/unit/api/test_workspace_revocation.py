"""Focused regression coverage for membership revocation side effects."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from sourcemind.api.v1 import workspaces
from sourcemind.core.dependencies import AuthenticatedUser
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
    session.commit.assert_awaited_once()
