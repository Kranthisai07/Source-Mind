# ruff: noqa: F811  (pytest fixtures are used by parameter name)
"""Workspace audit trail and admin export (D-067) against a real PostgreSQL on the runtime role.

Handlers are called directly with real sessions, so row-level security, the append-only trigger,
grants and transactions are real. Only the Redis rate limiter and the Clerk lookup are replaced.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.api.v1.workspace_admin import export_workspace_data, list_workspace_audit
from sourcemind.core.database import set_rls_user_context
from sourcemind.core.exceptions import (
    ContentTooLargeError,
    WorkspaceAccessDeniedError,
    WorkspaceNotFoundError,
)
from sourcemind.services.workspace import export as export_module
from tests.integration.test_graph_outbox_real_db import owner_engine  # noqa: F401  (fixture)
from tests.integration.test_role_management_real_db import _change
from tests.integration.test_security_foundation_real_db import (
    _actor,
    _add,
    _email,
    _revoke,
    _seed_security_tenants,
    member_add_env,  # noqa: F401  (fixture)
    owner_race_database_url,  # noqa: F401  (fixture used by owner_race_engine)
    owner_race_engine,  # noqa: F401  (fixture)
)
from tests.integration.test_workspace_invitations_real_db import (
    _accept,
    _fresh_email,
    _invite,
    _revoke_invite,
    clerk,  # noqa: F401  (autouse fixture: no Redis, no Clerk)
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def _no_export_rate_limit():
    with patch("sourcemind.api.v1.workspace_admin.enforce_rate_limit", new=AsyncMock()):
        yield


def _ok(result):
    assert not isinstance(result, Exception), result
    return result


async def _audit(engine, data, actor_id, **page):
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await set_rls_user_context(session, actor_id)
        try:
            return await list_workspace_audit(
                workspace_id=page.pop("workspace_id", data.target_workspace_id),
                db=session,
                current_user=_actor(data, actor_id),
                request_id="test",
                limit=page.get("limit", 50),
                before_id=page.get("before_id"),
            )
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            return exc


async def _export(engine, data, actor_id, workspace_id=None):
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await set_rls_user_context(session, actor_id)
        try:
            return await export_workspace_data(
                workspace_id=workspace_id or data.target_workspace_id,
                db=session,
                current_user=_actor(data, actor_id),
                request_id="test",
            )
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            return exc


async def _events(engine, data, as_user=None):
    """(event, actor, target_user, target_email, before, after), oldest first."""
    result = _ok(await _audit(engine, data, as_user or data.owner_id, limit=200))
    return [
        (
            row["event"],
            row["actor_user_id"],
            row["target_user_id"],
            row["target_email"],
            row["role_before"],
            row["role_after"],
        )
        for row in reversed(result.data)
    ]


async def test_every_audited_action_leaves_exactly_one_correct_event(
    supabase_engine, member_add_env
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    zero = data.zero_membership_id
    invitee = _email(data, zero)
    stranger = _fresh_email()
    assert await _events(supabase_engine, data) == []

    created = _ok(await _invite(supabase_engine, data, data.owner_id, invitee, "viewer"))
    _ok(await _accept(supabase_engine, data, zero, created.data.token))
    _ok(await _change(supabase_engine, data, data.owner_id, data.member_id, "admin"))
    assert await _revoke(supabase_engine, data, data.owner_id, zero) is None
    _ok(await _add(supabase_engine, data, data.owner_id, zero, "member"))
    pending = _ok(await _invite(supabase_engine, data, data.admin_id, stranger, "member"))
    assert await _revoke_invite(supabase_engine, data, data.owner_id, pending.data.id) is None

    assert await _events(supabase_engine, data) == [
        ("invitation_created", data.owner_id, None, invitee, None, "viewer"),
        ("invitation_accepted", zero, zero, invitee, None, "viewer"),
        ("role_changed", data.owner_id, data.member_id, None, "member", "admin"),
        ("member_removed", data.owner_id, zero, None, "viewer", None),
        ("member_added", data.owner_id, zero, None, None, "member"),
        ("invitation_created", data.admin_id, None, stranger, None, "member"),
        ("invitation_revoked", data.owner_id, None, stranger, None, "member"),
    ]


async def test_a_role_change_that_changes_nothing_or_is_refused_writes_nothing(
    supabase_engine,
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    _ok(await _change(supabase_engine, data, data.owner_id, data.member_id, "member"))  # no-op
    refused = await _change(supabase_engine, data, data.admin_id, data.member_id, "owner")
    assert isinstance(refused, WorkspaceAccessDeniedError)
    assert await _events(supabase_engine, data) == []


async def test_a_refused_invitation_leaves_no_event(supabase_engine) -> None:
    data = await _seed_security_tenants(supabase_engine)
    refused = await _invite(supabase_engine, data, data.admin_id, _fresh_email(), "admin")
    assert isinstance(refused, WorkspaceAccessDeniedError)
    assert await _events(supabase_engine, data) == []


async def test_a_founder_demoting_themselves_is_recorded_and_then_loses_access_to_the_log(
    supabase_engine,
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    _ok(await _change(supabase_engine, data, data.owner_id, data.owner_id, "member"))
    assert await _events(supabase_engine, data, as_user=data.backup_owner_id) == [
        ("role_changed", data.owner_id, data.owner_id, None, "owner", "member"),
    ]
    denied = await _audit(supabase_engine, data, data.owner_id)
    assert isinstance(denied, WorkspaceAccessDeniedError)


async def test_an_admin_removing_themselves_is_recorded(supabase_engine) -> None:
    data = await _seed_security_tenants(supabase_engine)
    assert await _revoke(supabase_engine, data, data.admin_id, data.admin_id) is None
    assert await _events(supabase_engine, data) == [
        ("member_removed", data.admin_id, data.admin_id, None, "admin", None),
    ]


async def test_only_founders_and_admins_can_read_the_log_or_export(supabase_engine) -> None:
    data = await _seed_security_tenants(supabase_engine)
    for user in (data.member_id, data.viewer_id, data.zero_membership_id):
        for call in (_audit, _export):
            denied = await call(supabase_engine, data, user)
            assert isinstance(denied, WorkspaceAccessDeniedError | WorkspaceNotFoundError), (
                call.__name__,
                denied,
            )
    # A founder or admin of a different workspace is an outsider here.
    other = data.other_workspace_member_id
    for call in (_audit, _export):
        denied = await call(supabase_engine, data, other)
        assert isinstance(denied, WorkspaceAccessDeniedError | WorkspaceNotFoundError), denied
    for user in (data.owner_id, data.admin_id):
        _ok(await _audit(supabase_engine, data, user))
        _ok(await _export(supabase_engine, data, user))


async def test_the_log_pages_newest_first_without_gaps_or_repeats(
    supabase_engine, owner_engine
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE users SET display_name = 'Founder One' WHERE id = :id"),
            {"id": data.owner_id},
        )
    for _ in range(5):
        _ok(await _invite(supabase_engine, data, data.owner_id, _fresh_email(), "member"))
    everything = _ok(await _audit(supabase_engine, data, data.owner_id, limit=200)).data
    first = _ok(await _audit(supabase_engine, data, data.owner_id, limit=2)).data
    second = _ok(
        await _audit(supabase_engine, data, data.owner_id, limit=2, before_id=first[-1]["id"])
    ).data
    assert [r["id"] for r in everything] == sorted((r["id"] for r in everything), reverse=True)
    assert [r["id"] for r in first + second] == [r["id"] for r in everything][:4]
    assert first[0]["actor_name"] == "Founder One"  # names are resolved for display


async def test_the_runtime_role_cannot_rewrite_or_delete_events_or_forge_them(
    supabase_engine,
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    _ok(await _invite(supabase_engine, data, data.owner_id, _fresh_email(), "member"))

    async def attempt(user_id, sql, **params):
        async with AsyncSession(supabase_engine) as session:
            await set_rls_user_context(session, user_id)
            await session.execute(text(sql), params)

    forged = (
        "INSERT INTO workspace_audit_events (workspace_id, actor_user_id, event) "
        "VALUES (:w, :a, :e)"
    )
    for sql, params, user in (
        ("UPDATE workspace_audit_events SET event = 'member_added'", {}, data.owner_id),
        ("DELETE FROM workspace_audit_events", {}, data.owner_id),
        # A teammate may not write a founder-level event, nor one in someone else's name.
        (forged, {"w": data.target_workspace_id, "a": data.member_id, "e": "role_changed"},
         data.member_id),
        (forged, {"w": data.target_workspace_id, "a": data.owner_id, "e": "role_changed"},
         data.admin_id),
        # An "accepted" event needs an invitation that this person accepted.
        (forged, {"w": data.target_workspace_id, "a": data.member_id, "e": "invitation_accepted"},
         data.member_id),
    ):
        with pytest.raises(DBAPIError):
            await attempt(user, sql, **params)


async def test_the_table_itself_refuses_updates_and_deletes_even_for_its_owner(
    supabase_engine, owner_engine
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    _ok(await _invite(supabase_engine, data, data.owner_id, _fresh_email(), "member"))

    class _Rollback(Exception):
        pass

    for change in ("UPDATE workspace_audit_events SET event = 'member_added'",
                   "DELETE FROM workspace_audit_events"):
        with pytest.raises(DBAPIError, match="append-only"):
            async with owner_engine.begin() as conn:
                await conn.execute(
                    text("ALTER TABLE workspace_audit_events NO FORCE ROW LEVEL SECURITY")
                )
                await conn.execute(text(change))
                raise _Rollback


def _keys(value, found=None):
    found = set() if found is None else found
    if isinstance(value, dict):
        for key, inner in value.items():
            found.add(key)
            _keys(inner, found)
    elif isinstance(value, list):
        for inner in value:
            _keys(inner, found)
    return found


async def test_the_export_holds_this_workspace_only_and_none_of_the_secrets(
    supabase_engine,
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    _ok(await _invite(supabase_engine, data, data.owner_id, _fresh_email(), "member"))
    export = _ok(await _export(supabase_engine, data, data.owner_id)).data

    assert export["workspace_id"] == str(data.target_workspace_id)
    memory_ids = {row["id"] for row in export["memories"]}
    assert str(data.target_memory_id) in memory_ids
    assert str(data.other_memory_id) not in json.dumps(export, default=str)
    assert str(data.other_workspace_id) not in json.dumps(export, default=str)
    forbidden = {"embedding", "clerk_id", "clerk_data", "token_hash", "key_hash", "token"}
    assert not (_keys(export) & forbidden), _keys(export) & forbidden
    assert {u["email"] for u in export["users"]} >= {_email(data, data.owner_id)}
    assert len(export["pending_invitations"]) == 1
    assert export["canonical_hash"]


async def test_exporting_is_recorded_and_a_refused_export_is_not(
    supabase_engine, monkeypatch
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    _ok(await _export(supabase_engine, data, data.admin_id))
    assert await _events(supabase_engine, data) == [
        ("workspace_exported", data.admin_id, None, None, None, None),
    ]
    monkeypatch.setattr(export_module, "MAX_ROWS_PER_DATASET", 0)
    too_big = await _export(supabase_engine, data, data.owner_id)
    assert isinstance(too_big, ContentTooLargeError)
    assert len(await _events(supabase_engine, data)) == 1
