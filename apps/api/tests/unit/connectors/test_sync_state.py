"""Unit tests for lease/fence helpers, the tick task and the beat schedule (no DB)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sourcemind.services.connectors import sync_state as ss


def _session(first_row):
    result = MagicMock()
    result.first.return_value = first_row
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.mark.unit
async def test_acquire_returns_none_and_rolls_back_when_the_lease_is_held() -> None:
    session = _session(None)

    assert await ss.acquire_lease(session, uuid.uuid4(), lease_seconds=600) is None

    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()


@pytest.mark.unit
async def test_acquire_returns_the_new_fence() -> None:
    session = _session((7,))
    cid = uuid.uuid4()

    token = await ss.acquire_lease(session, cid, lease_seconds=600, owner="w1")

    assert token == ss.LeaseToken(cid, 7, "w1")
    session.commit.assert_awaited_once()
    sql = str(session.execute.await_args.args[0])
    assert "fence = fence + 1" in sql and "sync_lease_expires_at IS NULL OR" in sql


@pytest.mark.unit
async def test_renew_is_fenced_on_token_and_active_status_and_loss_rolls_back() -> None:
    session = _session(None)
    token = ss.LeaseToken(uuid.uuid4(), 3, "w")

    with pytest.raises(ss.LeaseLostError):
        await ss.renew_lease(session, token, lease_seconds=600)

    session.rollback.assert_awaited_once()
    sql = str(session.execute.await_args.args[0])
    assert "fence = :fence" in sql and "status = 'active'" in sql and "RETURNING 1" in sql
    assert session.execute.await_args.args[1]["fence"] == 3


@pytest.mark.unit
async def test_renew_succeeds_without_rollback_when_a_row_is_returned() -> None:
    session = _session((1,))

    await ss.renew_lease(session, ss.LeaseToken(uuid.uuid4(), 1, "w"), lease_seconds=60)

    session.rollback.assert_not_awaited()


@pytest.mark.unit
async def test_cursor_upsert_rejects_unknown_columns() -> None:
    with pytest.raises(ValueError):
        await ss.upsert_cursor(
            AsyncMock(),
            workspace_id=uuid.uuid4(),
            connector_id=uuid.uuid4(),
            repo="a/b",
            scope="issues",
            bogus=1,
        )


@pytest.mark.unit
async def test_inventory_for_an_unknown_scope_changes_nothing() -> None:
    session = AsyncMock()

    assert (
        await ss.apply_inventory(
            session, workspace_id=uuid.uuid4(), repo="a/b", scope="nope", seen_source_ids={"x"}
        )
        == []
    )
    session.execute.assert_not_awaited()


@pytest.mark.unit
def test_every_scope_owns_exactly_the_types_it_lists_completely() -> None:
    from sourcemind.connectors.github.contracts import SCOPES

    assert set(ss.SCOPE_INVENTORY_TYPES) == set(SCOPES)
    owned = [t for types in ss.SCOPE_INVENTORY_TYPES.values() for t in types]
    assert len(owned) == len(set(owned))  # no segment type is judged by two scopes


@pytest.mark.unit
def test_beat_schedule_exists_only_when_the_scheduler_is_enabled() -> None:
    from sourcemind.workers.celery_app import connector_beat_schedule

    off = SimpleNamespace(connector_scheduler_enabled=False, connector_sync_tick_seconds=300)
    on = SimpleNamespace(connector_scheduler_enabled=True, connector_sync_tick_seconds=120)

    assert connector_beat_schedule(off) == {}
    entry = connector_beat_schedule(on)["connector-sync-tick"]
    assert entry["task"].endswith("dispatch_due_syncs") and entry["schedule"] == 120.0


@pytest.mark.unit
def test_tick_enqueues_a_scheduled_sync_for_every_due_connector() -> None:
    from sourcemind.workers import connector_tasks

    cid, ws, creator = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    rows = [
        {"connector_id": cid, "workspace_id": ws, "created_by": creator,
         "creator_active": True, "acting_admin": uuid.uuid4()}
    ]
    with (
        patch.object(connector_tasks, "_list_due", new=AsyncMock(return_value=rows)),
        patch.object(connector_tasks.sync_connector, "apply_async") as enqueue,
    ):
        result = connector_tasks.dispatch_due_syncs()

    assert result == {"dispatched": 1}
    kwargs = enqueue.call_args.kwargs["kwargs"]
    assert kwargs == {
        "connector_id": str(cid),
        "workspace_id": str(ws),
        "user_id": str(creator),
        "trigger": "scheduled",
    }


@pytest.mark.unit
async def test_a_run_without_a_known_creator_is_refused_not_guessed() -> None:
    from sourcemind.workers import connector_tasks

    out = await connector_tasks._run_sync(uuid.uuid4(), uuid.uuid4(), None, "scheduled", "incremental")

    assert out["status"] == "rejected" and out["error_code"] == "creator_unknown"


@pytest.mark.unit
def test_retry_backoff_grows_and_is_bounded() -> None:
    from sourcemind.workers.connector_tasks import _backoff_seconds

    assert 30 <= _backoff_seconds(0) <= 45
    assert _backoff_seconds(10) <= 1350
    assert _backoff_seconds(3) > _backoff_seconds(0) - 1


@pytest.mark.unit
def test_tick_uses_an_acting_admin_when_the_creator_left_and_skips_orphans() -> None:
    from sourcemind.workers import connector_tasks

    cid, ws, creator, admin = (uuid.uuid4() for _ in range(4))
    orphan = uuid.uuid4()
    rows = [
        {"connector_id": cid, "workspace_id": ws, "created_by": creator,
         "creator_active": False, "acting_admin": admin},
        {"connector_id": orphan, "workspace_id": ws, "created_by": creator,
         "creator_active": False, "acting_admin": None},
    ]
    with (
        patch.object(connector_tasks, "_list_due", new=AsyncMock(return_value=rows)),
        patch.object(connector_tasks.sync_connector, "apply_async") as enqueue,
    ):
        result = connector_tasks.dispatch_due_syncs()

    assert result == {"dispatched": 1}
    assert enqueue.call_count == 1
    assert enqueue.call_args.kwargs["kwargs"]["user_id"] == str(admin)  # never the departed creator
