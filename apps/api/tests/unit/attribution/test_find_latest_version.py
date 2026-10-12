"""Unit coverage for superseded-version resolution (D-025).

The session is a mock: the first execute() answers the "is this row current?"
probe, the second answers the aggregate over descendants. The recursive CTE and
RLS behaviour are covered against real PostgreSQL in
tests/integration/test_attribution_history_real_db.py.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sourcemind.core.exceptions import (
    MemoryNotFoundError,
    MemoryVersionStaleError,
    WorkspaceAccessDeniedError,
)
from sourcemind.services.attribution.versioning import (
    SupersessionReason,
    find_latest_version,
    unavailable_version_error,
)

MEMORY_ID = uuid.uuid4()
WS_ID = uuid.uuid4()
USER_ID = uuid.uuid4()
LATEST_ID = uuid.uuid4()
_ACCESS = "sourcemind.services.attribution.versioning.require_memory_access"


def _result(*, first=None, one=None) -> MagicMock:
    result = MagicMock()
    result.first.return_value = first
    result.one.return_value = one
    return result


def _own(current: bool) -> MagicMock:
    return _result(first=SimpleNamespace(current_version=current))


def _summary(
    total: int, live: int, only_id: uuid.UUID | None = None, cut_off: int = 0
) -> MagicMock:
    return _result(
        one=SimpleNamespace(
            total=total,
            live=live,
            only_id=str(only_id) if only_id else None,
            cut_off=cut_off,
        )
    )


def _session(*results: MagicMock) -> AsyncMock:
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=list(results))
    return session


@pytest.mark.unit
@pytest.mark.asyncio
async def test_current_memory_has_no_supersession_reason() -> None:
    session = _session(_own(True))
    latest = await find_latest_version(session, MEMORY_ID, WS_ID, user_id=USER_ID)
    assert (latest.reason, latest.latest_memory_id) == (None, None)
    assert session.execute.await_count == 1  # no descendant walk for a current row


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unique_authorized_successor_is_returned_after_an_access_check() -> None:
    session = _session(_own(False), _summary(2, 1, LATEST_ID))
    with patch(_ACCESS, new=AsyncMock()) as access:
        latest = await find_latest_version(session, MEMORY_ID, WS_ID, user_id=USER_ID)
    assert (latest.reason, latest.latest_memory_id) == (SupersessionReason.SUPERSEDED, LATEST_ID)
    args = access.await_args.args
    assert args[1] == USER_ID and args[2] == LATEST_ID  # authorization is on THAT id
    assert args[3].value == "read"


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("denial", [MemoryNotFoundError("x"), WorkspaceAccessDeniedError("x")])
async def test_unauthorized_successor_downgrades_without_leaking_its_id(denial) -> None:
    session = _session(_own(False), _summary(1, 1, LATEST_ID))
    with patch(_ACCESS, new=AsyncMock(side_effect=denial)):
        latest = await find_latest_version(session, MEMORY_ID, WS_ID, user_id=USER_ID)
    assert latest.reason is SupersessionReason.SUPERSEDED_WITHOUT_SUCCESSOR
    assert latest.latest_memory_id is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unknown_caller_never_receives_a_successor_id() -> None:
    session = _session(_own(False), _summary(1, 1, LATEST_ID))
    with patch(_ACCESS, new=AsyncMock()) as access:
        latest = await find_latest_version(session, MEMORY_ID, WS_ID, user_id=None)
    assert latest.latest_memory_id is None
    access.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fork_is_ambiguous_and_never_picks_one() -> None:
    session = _session(_own(False), _summary(3, 2, LATEST_ID))
    with patch(_ACCESS, new=AsyncMock()) as access:
        latest = await find_latest_version(session, MEMORY_ID, WS_ID, user_id=USER_ID)
    assert latest.reason is SupersessionReason.AMBIGUOUS_SUCCESSOR
    assert latest.latest_memory_id is None
    access.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_deleted_or_retired_descendants_mean_no_current_version() -> None:
    session = _session(_own(False), _summary(2, 0))
    latest = await find_latest_version(session, MEMORY_ID, WS_ID, user_id=USER_ID)
    assert latest.reason is SupersessionReason.NO_CURRENT_VERSION
    assert latest.latest_memory_id is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_merge_retired_row_without_descendants() -> None:
    session = _session(_own(False), _summary(0, 0))
    latest = await find_latest_version(session, MEMORY_ID, WS_ID, user_id=USER_ID)
    assert latest.reason is SupersessionReason.SUPERSEDED_WITHOUT_SUCCESSOR
    assert latest.latest_memory_id is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_error_is_409_with_details_for_a_superseded_row() -> None:
    row = SimpleNamespace(workspace_id=WS_ID, current_version=False, deleted_at=None)
    session = _session(_result(first=row), _own(False), _summary(1, 1, LATEST_ID))
    with patch(_ACCESS, new=AsyncMock()):
        error = await unavailable_version_error(session, MEMORY_ID, user_id=USER_ID)
    assert isinstance(error, MemoryVersionStaleError)
    assert error.http_status == 409 and error.code == "SM035"
    assert error.details == {
        "memory_id": str(MEMORY_ID),
        "latest_memory_id": str(LATEST_ID),
        "reason": "superseded",
    }


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "row",
    [
        None,
        SimpleNamespace(workspace_id=WS_ID, current_version=False, deleted_at="2026-01-01"),
    ],
    ids=["missing", "deleted"],
)
async def test_missing_or_deleted_rows_stay_the_generic_404(row) -> None:
    session = _session(_result(first=row))
    error = await unavailable_version_error(session, MEMORY_ID, user_id=USER_ID)
    assert isinstance(error, MemoryNotFoundError)
    assert error.code == "SM020"
    assert session.execute.await_count == 1  # no descendant lookup, nothing leaked


# ── Truncated walks (more than MAX_CHAIN_DEPTH versions) ─────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("live", "only_id"), [(0, None), (1, LATEST_ID)], ids=["no-live-head", "one-live-head"]
)
async def test_truncated_walk_never_claims_no_current_version_or_an_id(live, only_id) -> None:
    session = _session(_own(False), _summary(10, live, only_id, cut_off=1))
    with patch(_ACCESS, new=AsyncMock()) as access:
        latest = await find_latest_version(session, MEMORY_ID, WS_ID, user_id=USER_ID)
    assert latest.reason is SupersessionReason.SUPERSEDED_WITHOUT_SUCCESSOR
    assert latest.latest_memory_id is None
    assert latest.truncated is True
    access.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_fork_seen_in_a_truncated_walk_is_still_ambiguous() -> None:
    session = _session(_own(False), _summary(10, 2, LATEST_ID, cut_off=1))
    latest = await find_latest_version(session, MEMORY_ID, WS_ID, user_id=USER_ID)
    assert latest.reason is SupersessionReason.AMBIGUOUS_SUCCESSOR
    assert latest.truncated is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stale_error_flags_truncation_only_when_it_happened() -> None:
    row = SimpleNamespace(workspace_id=WS_ID, current_version=False, deleted_at=None)
    cut = _session(_result(first=row), _own(False), _summary(10, 0, cut_off=1))
    error = await unavailable_version_error(cut, MEMORY_ID, user_id=USER_ID)
    assert error.details == {
        "memory_id": str(MEMORY_ID),
        "latest_memory_id": None,
        "reason": "superseded_without_successor",
        "truncated": True,
    }
    normal = _session(_result(first=row), _own(False), _summary(0, 0))
    error = await unavailable_version_error(normal, MEMORY_ID, user_id=USER_ID)
    assert "truncated" not in error.details
