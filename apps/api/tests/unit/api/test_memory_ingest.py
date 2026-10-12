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


async def _submit(receiver_result: dict):
    receiver = AsyncMock(return_value=receiver_result)
    user = AuthenticatedUser(user_id=uuid.uuid4(), clerk_id="pilot", email="pilot@example.com")
    with (
        patch(
            "sourcemind.api.v1.memories.require_workspace_permission",
            new=AsyncMock(return_value="member"),
        ),
        patch("sourcemind.api.v1.memories.enforce_rate_limit", new=AsyncMock()),
        patch("sourcemind.services.ingestion.receiver.receive", new=receiver),
    ):
        return await create_memory(
            body=MemoryCreate(content="Same text twice."),
            db=AsyncMock(),
            current_user=user,
            request_id=str(uuid.uuid4()),
            idempotency_key=str(uuid.uuid4()),
            workspace_id=uuid.uuid4(),
        )


def _receiver_result(**extra) -> dict:
    return {
        "job_id": "job-1",
        "document_id": str(uuid.uuid4()),
        "status": "completed",
        "message": "Document already ingested. Returning existing record.",
        "memory_count": 2,
        **extra,
    }


@pytest.mark.unit
async def test_create_memory_reports_a_submission_that_matched_an_existing_document() -> None:
    response = await _submit(_receiver_result(already_exists=True))
    assert response.data.already_exists is True
    # Existing semantics are untouched: same status, message and counts.
    assert response.data.status == "completed"
    assert response.data.message == "Document already ingested. Returning existing record."
    assert response.data.memory_count == 2
    assert response.model_dump(mode="json")["data"]["already_exists"] is True


@pytest.mark.unit
@pytest.mark.parametrize("extra", [{"already_exists": False}, {}], ids=["false", "absent"])
async def test_create_memory_defaults_already_exists_to_false(extra) -> None:
    response = await _submit(_receiver_result(**extra))
    assert response.data.already_exists is False


@pytest.mark.unit
def test_polling_schema_defaults_already_exists_to_false() -> None:
    from sourcemind.schemas.memory import IngestionJobResponse

    polled = IngestionJobResponse(
        job_id="j", document_id=uuid.uuid4(), status="completed", message="Ingestion complete."
    )
    assert polled.already_exists is False
