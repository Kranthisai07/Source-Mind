"""recover_pending_dispatch: re-dispatch a committed document by id (D-021 round 3).

Used by the GitHub connector when an artifact link already exists but its
document was never confirmed queued (publication failed after the Document
and link committed). It reuses the dispatch state machine of receive():
row lock, the same ``ingestion_job_id`` as the Celery task id, no republish
once ``queued``, and ``uncertain`` + re-raise on a broker error.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from sourcemind.models.document import IngestionStatus

PUBLISH = "sourcemind.workers.ingestion.process_document.apply_async"


class _Session:
    def __init__(self, doc: SimpleNamespace) -> None:
        self.doc = doc
        self.commits = 0
        self.locked_selects: list[str] = []

    async def execute(self, stmt, *_a, **_k):
        self.locked_selects.append(str(stmt))
        result = MagicMock()
        result.scalar_one.return_value = self.doc
        return result

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1


def _published(**kwargs):
    return SimpleNamespace(id=kwargs["task_id"])


def _stranded_doc(dispatch_state: str = "uncertain") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        submitter_id=uuid.uuid4(),
        ingestion_job_id="job-" + uuid.uuid4().hex,
        ingestion_status=IngestionStatus.PENDING,
        source_type="text",
        memory_count=0,
        pipeline_data={
            "current_stage": "queued",
            "dispatch_state": dispatch_state,
            "dispatch_attempts": 1,
            "attribution": {"mode": "external", "github_user_id": 101, "link_user_id": None},
        },
    )


async def _recover(session: _Session, workspace_id: uuid.UUID, publish):
    from sourcemind.services.ingestion.receiver import recover_pending_dispatch

    with patch(PUBLISH, side_effect=publish) as apply_async:
        response = await recover_pending_dispatch(session, session.doc.id, workspace_id)
    return response, apply_async


@pytest.mark.unit
async def test_recovery_publishes_once_with_the_original_task_id() -> None:
    doc = _stranded_doc("uncertain")
    session, ws = _Session(doc), uuid.uuid4()
    task_id = doc.ingestion_job_id

    response, apply_async = await _recover(session, ws, _published)

    apply_async.assert_called_once()
    kwargs = apply_async.call_args.kwargs
    assert kwargs["task_id"] == task_id
    assert kwargs["kwargs"] == {
        "document_id": str(doc.id),
        "workspace_id": str(ws),
        "user_id": str(doc.submitter_id),
    }
    assert doc.pipeline_data["dispatch_state"] == "queued"
    assert doc.pipeline_data["dispatch_attempts"] == 2
    # The sync-time attribution decision is untouched: no second decision.
    assert doc.pipeline_data["attribution"]["github_user_id"] == 101
    assert response["job_id"] == task_id
    assert response["already_exists"] is False
    assert "FOR UPDATE" in session.locked_selects[0].upper()


@pytest.mark.unit
async def test_recovery_does_not_republish_after_success() -> None:
    doc = _stranded_doc("uncertain")
    session, ws = _Session(doc), uuid.uuid4()
    await _recover(session, ws, _published)

    response, apply_async = await _recover(session, ws, _published)

    apply_async.assert_not_called()
    assert response["already_exists"] is True
    assert doc.pipeline_data["dispatch_state"] == "queued"


@pytest.mark.unit
async def test_already_queued_document_is_not_republished() -> None:
    doc = _stranded_doc("queued")
    session = _Session(doc)

    _response, apply_async = await _recover(session, uuid.uuid4(), lambda **_k: None)

    apply_async.assert_not_called()


@pytest.mark.unit
async def test_broker_error_marks_uncertain_reraises_and_retry_reuses_task_id() -> None:
    doc = _stranded_doc("orphaned")
    session, ws = _Session(doc), uuid.uuid4()
    task_id = doc.ingestion_job_id

    def broker_down(**_kwargs):
        raise ConnectionError("broker unavailable")

    with pytest.raises(ConnectionError, match="broker unavailable"):
        await _recover(session, ws, broker_down)
    assert doc.pipeline_data["dispatch_state"] == "uncertain"
    assert doc.ingestion_job_id == task_id

    _response, apply_async = await _recover(
        session, ws, _published
    )
    apply_async.assert_called_once()
    assert apply_async.call_args.kwargs["task_id"] == task_id
    assert doc.pipeline_data["dispatch_state"] == "queued"
