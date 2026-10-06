"""A committed artifact link must not hide an undispatched document (D-021 round 3).

Since D-021 the ArtifactLink commits with its Document, BEFORE the ingestion
task is published. If publication then fails, the document stays
``pending`` (dispatch_state ``uncertain``) while the link already exists, so
an existence-only dedup check would skip the artifact forever. The connector
now checks the STATE of the linked documents and re-dispatches a pending one
by DOCUMENT ID (``recover_pending_dispatch``), never through
``receive(content)``.

The same query replaces ``scalar_one_or_none()``, which raised
``MultipleResultsFound`` once ``backfill_artifact_links`` had cloned the link
for a second memory (a defect that predates D-021).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import MultipleResultsFound

from sourcemind.connectors.github.mapper import ConnectorDocument
from sourcemind.models.document import IngestionStatus

CONNECTOR = "sourcemind.connectors.github.connector"


def _doc() -> ConnectorDocument:
    return ConnectorDocument(
        source_tool="github",
        source_type="commit",
        source_id="acme/repo/commits/" + uuid.uuid4().hex,
        source_author="alice",
        title="t",
        content="edited since the first sync " + uuid.uuid4().hex,
        idempotency_key=uuid.uuid4().hex,
        metadata={"author": {"github_user_id": 101, "login": "alice", "kind": "github_account"}},
    )


def _row(document_id, status, deleted_at=None, dispatch_state="uncertain"):
    return SimpleNamespace(
        document_id=document_id,
        ingestion_status=status,
        deleted_at=deleted_at,
        dispatch_state=dispatch_state,
    )


def _session(rows: list[SimpleNamespace]) -> AsyncMock:
    """Answers the dedup query both ways, so current and fixed code see the same DB.

    ``.all()`` returns every link row; ``scalar_one_or_none()`` behaves like
    SQLAlchemy over those rows (None, the row, or MultipleResultsFound).
    """
    result = MagicMock()
    result.all.return_value = rows
    if len(rows) > 1:
        result.scalar_one_or_none.side_effect = MultipleResultsFound("multiple rows")
    else:
        result.scalar_one_or_none.return_value = rows[0] if rows else None
    session = AsyncMock()
    session.add = MagicMock()
    session.execute = AsyncMock(return_value=result)
    return session


async def _ingest(rows, *, recover_side_effect=None):
    from sourcemind.connectors.github.connector import GitHubConnector

    workspace_id = uuid.uuid4()
    session = _session(rows)
    connector = GitHubConnector(
        config=MagicMock(),
        auth=AsyncMock(),
        session=session,
        workspace_id=workspace_id,
        user_id=uuid.uuid4(),
    )
    receive = AsyncMock(return_value={"already_exists": False, "document_id": str(uuid.uuid4())})
    resolver = AsyncMock(return_value=SimpleNamespace(user_id=None, status="unlinked"))
    recover = AsyncMock(side_effect=recover_side_effect)
    with (
        patch(f"{CONNECTOR}.receive", new=receive),
        patch(f"{CONNECTOR}.resolve_github_link", new=resolver),
        patch(f"{CONNECTOR}.recover_pending_dispatch", new=recover, create=True),
    ):
        result = await connector._ingest(_doc())
    return result, receive, recover, resolver, session, workspace_id


@pytest.mark.unit
async def test_persisted_link_with_undispatched_document_is_recovered_by_id() -> None:
    document_id = uuid.uuid4()

    result, receive, recover, resolver, session, ws = await _ingest(
        [_row(document_id, IngestionStatus.PENDING)]
    )

    assert result is False
    recover.assert_awaited_once()
    assert recover.await_args.args == (session, document_id, ws)
    receive.assert_not_awaited()  # never re-ingested by content
    resolver.assert_not_awaited()  # no second attribution decision


@pytest.mark.unit
@pytest.mark.parametrize("state", ["orphaned", "publishing", "uncertain"])
async def test_every_unconfirmed_dispatch_state_is_recovered(state) -> None:
    document_id = uuid.uuid4()

    result, receive, recover, *_ = await _ingest(
        [_row(document_id, IngestionStatus.PENDING, dispatch_state=state)]
    )

    assert result is False
    recover.assert_awaited_once()
    assert recover.await_args.args[1] == document_id
    receive.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.parametrize("state", ["queued", "work_started", "not_required", None])
async def test_pending_document_the_broker_already_accepted_is_left_alone(state) -> None:
    # The worker holds the document's row lock for its whole pipeline while the
    # COMMITTED status still reads `pending`. Recovery starts with a blocking
    # SELECT ... FOR UPDATE on that row, so a re-sync would stall behind every
    # in-flight document. Only a document whose publication is NOT confirmed
    # (orphaned/publishing/uncertain) is touched.
    result, receive, recover, *_ = await _ingest(
        [_row(uuid.uuid4(), IngestionStatus.PENDING, dispatch_state=state)]
    )

    assert result is False
    recover.assert_not_awaited()
    receive.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.parametrize(
    "status", [IngestionStatus.PROCESSING, IngestionStatus.COMPLETED, IngestionStatus.FAILED]
)
async def test_dispatched_or_finished_document_is_skipped_without_publication(status) -> None:
    result, receive, recover, _resolver, _s, _ws = await _ingest([_row(uuid.uuid4(), status)])

    assert result is False
    recover.assert_not_awaited()
    receive.assert_not_awaited()


@pytest.mark.unit
async def test_deleted_pending_document_is_not_recovered() -> None:
    result, receive, recover, *_ = await _ingest(
        [_row(uuid.uuid4(), IngestionStatus.PENDING, deleted_at="2026-10-01")]
    )

    assert result is False
    recover.assert_not_awaited()
    receive.assert_not_awaited()


@pytest.mark.unit
async def test_legacy_link_without_document_counts_as_existing() -> None:
    result, receive, recover, *_ = await _ingest([_row(None, None)])

    assert result is False
    recover.assert_not_awaited()
    receive.assert_not_awaited()


@pytest.mark.unit
async def test_anchor_and_clone_of_a_completed_document_skip_without_error() -> None:
    document_id = uuid.uuid4()
    rows = [_row(document_id, IngestionStatus.COMPLETED)] * 2  # anchor + clone

    first = await _ingest(rows)
    second = await _ingest(rows)

    for result, receive, recover, *_ in (first, second):
        assert result is False
        recover.assert_not_awaited()
        receive.assert_not_awaited()


@pytest.mark.unit
async def test_several_rows_for_one_pending_document_recover_it_exactly_once() -> None:
    document_id = uuid.uuid4()
    rows = [_row(document_id, IngestionStatus.PENDING)] * 2  # anchor + clone

    result, receive, recover, *_ = await _ingest(rows)

    assert result is False
    recover.assert_awaited_once()
    assert recover.await_args.args[1] == document_id
    receive.assert_not_awaited()


@pytest.mark.unit
async def test_broker_still_down_fails_like_any_ingest_failure() -> None:
    document_id = uuid.uuid4()

    with pytest.raises(RuntimeError, match="broker unavailable"):
        await _ingest(
            [_row(document_id, IngestionStatus.PENDING)],
            recover_side_effect=RuntimeError("broker unavailable"),
        )


@pytest.mark.unit
async def test_no_link_ingests_through_the_receiver_as_before() -> None:
    result, receive, recover, resolver, *_ = await _ingest([])

    assert result is True
    receive.assert_awaited_once()
    resolver.assert_awaited_once()
    recover.assert_not_awaited()
