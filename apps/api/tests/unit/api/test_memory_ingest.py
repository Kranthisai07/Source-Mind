from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from sourcemind.api.v1.memories import create_memory
from sourcemind.core.dependencies import AuthenticatedUser
from sourcemind.schemas.memory import MemoryCreate


@pytest.mark.unit
async def test_create_memory_forwards_verbatim_mode_and_exact_content() -> None:
    workspace_id = uuid.uuid4()
    document_id = uuid.uuid4()
    content = "  Exact content.\nSecond line.  "
    receiver = AsyncMock(
        return_value={
            "job_id": "pilot-job",
            "document_id": str(document_id),
            "status": "queued",
            "message": "queued",
        }
    )
    user = AuthenticatedUser(
        user_id=uuid.uuid4(),
        clerk_id="pilot",
        email="pilot@example.com",
    )

    with (
        patch(
            "sourcemind.api.v1.memories.require_workspace_permission",
            new=AsyncMock(return_value="member"),
        ),
        patch(
            "sourcemind.api.v1.memories.enforce_rate_limit",
            new=AsyncMock(),
        ),
        patch("sourcemind.services.ingestion.receiver.receive", new=receiver),
    ):
        await create_memory(
            body=MemoryCreate(content=content, ingestion_mode="verbatim"),
            db=AsyncMock(),
            current_user=user,
            request_id=str(uuid.uuid4()),
            idempotency_key=str(uuid.uuid4()),
            workspace_id=workspace_id,
        )

    assert receiver.await_args.kwargs["content"] == content
    assert receiver.await_args.kwargs["ingestion_mode"] == "verbatim"
