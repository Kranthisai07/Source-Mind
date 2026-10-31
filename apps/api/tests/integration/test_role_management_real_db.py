# ruff: noqa: F811  (pytest fixtures are used by parameter name)
"""Changing a member's role (D-064), against a real PostgreSQL on the restricted runtime role.

The handler is called directly with real sessions, so row-level security, row locks and the
last-owner trigger are all real. Only the Redis rate limiter is replaced.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.api.v1.workspaces import change_workspace_member_role, revoke_workspace_member
from sourcemind.core.database import set_rls_user_context, set_rls_workspace_context
from sourcemind.core.exceptions import (
    UserNotFoundError,
    WorkspaceAccessDeniedError,
    WorkspaceNotFoundError,
)
from sourcemind.models.document import Document
from sourcemind.models.workspace import WorkspaceMember
from sourcemind.schemas.workspace import WorkspaceMemberRoleUpdate
from sourcemind.services.ingestion.lifecycle import ROLE_CHANGE_INGESTION_ERROR
from sourcemind.services.workspace.roles import ADMIN_CEILING, LAST_FOUNDER, ROLE_CHANGED
from tests.integration.test_security_foundation_real_db import (
    _actor,
    _seed_security_tenants,
    owner_race_database_url,  # noqa: F401  (fixture used by owner_race_engine)
    owner_race_engine,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def _no_redis_rate_limit():
    with patch("sourcemind.api.v1.workspaces.enforce_rate_limit", new=AsyncMock()):
        yield


async def _change(engine, data, actor_id, target_id, role):
    """Run the endpoint; return the response, or the exception it raised."""
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await set_rls_user_context(session, actor_id)
        try:
            return await change_workspace_member_role(
                workspace_id=data.target_workspace_id,
                user_id=target_id,
                body=WorkspaceMemberRoleUpdate(role=role),
                db=session,
                current_user=_actor(data, actor_id),
                request_id="test",
                idempotency_key=str(uuid.uuid4()),
            )
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            return exc


async def _role_of(engine, data, user_id) -> str | None:
    """The stored role of an active member, read as the workspace's first owner."""
    async with AsyncSession(engine) as session:
        await set_rls_user_context(session, data.owner_id)
        await set_rls_workspace_context(session, data.target_workspace_id)
        row = await session.scalar(
            select(WorkspaceMember.role).where(
                WorkspaceMember.workspace_id == data.target_workspace_id,
                WorkspaceMember.user_id == user_id,
                WorkspaceMember.status == "active",
            )
        )
        return None if row is None else str(row)


async def _active_owner_count(engine, data, as_user) -> int:
    async with AsyncSession(engine) as session:
        await set_rls_user_context(session, as_user)
        await set_rls_workspace_context(session, data.target_workspace_id)
        return int(
            await session.scalar(
                select(func.count())
                .select_from(WorkspaceMember)
                .where(
                    WorkspaceMember.workspace_id == data.target_workspace_id,
                    WorkspaceMember.role == "owner",
                    WorkspaceMember.status == "active",
                    WorkspaceMember.departed_at.is_(None),
                )
            )
        )


async def test_a_founder_can_promote_a_teammate_to_admin_and_a_viewer_to_founder(
    supabase_engine,
) -> None:
    data = await _seed_security_tenants(supabase_engine)

    promoted = await _change(supabase_engine, data, data.owner_id, data.member_id, "admin")
    assert not isinstance(promoted, Exception), promoted
    assert promoted.data.role == "admin"
    assert await _role_of(supabase_engine, data, data.member_id) == "admin"

    cofounder = await _change(supabase_engine, data, data.owner_id, data.viewer_id, "owner")
    assert not isinstance(cofounder, Exception), cofounder
    assert await _role_of(supabase_engine, data, data.viewer_id) == "owner"
    assert await _active_owner_count(supabase_engine, data, data.owner_id) == 3


async def test_an_admin_cannot_escalate_anyone_including_themselves(supabase_engine) -> None:
    data = await _seed_security_tenants(supabase_engine)
    attempts = [
        (data.member_id, "admin"),  # grant admin
        (data.member_id, "owner"),  # grant founder
        (data.viewer_id, "admin"),
        (data.admin_id, "owner"),  # promote self
        (data.admin_id, "member"),  # change an admin (here: self)
        (data.owner_id, "member"),  # touch a founder
        (data.backup_owner_id, "viewer"),
    ]
    for target, role in attempts:
        outcome = await _change(supabase_engine, data, data.admin_id, target, role)
        assert isinstance(outcome, WorkspaceAccessDeniedError), (target, role, outcome)
        assert ADMIN_CEILING in str(outcome)

    assert await _role_of(supabase_engine, data, data.member_id) == "member"
    assert await _role_of(supabase_engine, data, data.viewer_id) == "viewer"
    assert await _role_of(supabase_engine, data, data.admin_id) == "admin"
    assert await _role_of(supabase_engine, data, data.owner_id) == "owner"
    assert await _role_of(supabase_engine, data, data.backup_owner_id) == "owner"


async def test_an_admin_can_move_people_between_teammate_and_viewer_and_it_stops_queued_work(
    supabase_engine,
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    async with AsyncSession(supabase_engine, expire_on_commit=False) as session:
        await set_rls_user_context(session, data.owner_id)
        await set_rls_workspace_context(session, data.target_workspace_id)
        document = Document(
            workspace_id=data.target_workspace_id,
            submitter_id=data.member_id,
            source_type="text",
            sha256_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            ingestion_status="pending",
        )
        session.add(document)
        await session.commit()
        document_id = document.id

    down = await _change(supabase_engine, data, data.admin_id, data.member_id, "viewer")
    assert not isinstance(down, Exception), down
    assert down.data.role == "viewer"

    async with AsyncSession(supabase_engine) as session:
        await set_rls_user_context(session, data.owner_id)
        await set_rls_workspace_context(session, data.target_workspace_id)
        stored = await session.scalar(select(Document).where(Document.id == document_id))
        assert stored.ingestion_status == "failed"
        assert stored.error_message == ROLE_CHANGE_INGESTION_ERROR

    up = await _change(supabase_engine, data, data.admin_id, data.member_id, "member")
    assert not isinstance(up, Exception), up
    assert await _role_of(supabase_engine, data, data.member_id) == "member"


async def test_teammates_and_viewers_cannot_change_roles(supabase_engine) -> None:
    data = await _seed_security_tenants(supabase_engine)
    for actor in (data.member_id, data.viewer_id):
        outcome = await _change(supabase_engine, data, actor, data.viewer_id, "member")
        assert isinstance(outcome, WorkspaceAccessDeniedError), (actor, outcome)
    assert await _role_of(supabase_engine, data, data.viewer_id) == "viewer"


async def test_outsiders_find_nothing(supabase_engine) -> None:
    data = await _seed_security_tenants(supabase_engine)
    for actor in (data.zero_membership_id, data.other_workspace_member_id):
        outcome = await _change(supabase_engine, data, actor, data.member_id, "viewer")
        assert isinstance(outcome, WorkspaceNotFoundError), (actor, outcome)
    assert await _role_of(supabase_engine, data, data.member_id) == "member"


async def test_unknown_and_departed_targets_are_not_found(supabase_engine) -> None:
    data = await _seed_security_tenants(supabase_engine)
    missing = await _change(supabase_engine, data, data.owner_id, uuid.uuid4(), "member")
    assert isinstance(missing, UserNotFoundError)
    # Someone from another workspace is also just "not a member here".
    other = await _change(
        supabase_engine, data, data.owner_id, data.other_workspace_member_id, "admin"
    )
    assert isinstance(other, UserNotFoundError)

    async with AsyncSession(supabase_engine) as session:
        await set_rls_user_context(session, data.owner_id)
        await set_rls_workspace_context(session, data.target_workspace_id)
        await revoke_workspace_member(
            workspace_id=data.target_workspace_id,
            user_id=data.member_id,
            db=session,
            current_user=_actor(data, data.owner_id),
            idempotency_key=str(uuid.uuid4()),
        )
    departed = await _change(supabase_engine, data, data.owner_id, data.member_id, "admin")
    assert isinstance(departed, UserNotFoundError)


async def test_setting_the_role_a_member_already_has_is_a_harmless_no_op(
    supabase_engine,
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    same = await _change(supabase_engine, data, data.owner_id, data.member_id, "member")
    assert not isinstance(same, Exception), same
    assert same.data.role == "member"
    assert await _role_of(supabase_engine, data, data.member_id) == "member"


async def test_the_last_founder_cannot_step_down_until_another_is_named(
    supabase_engine,
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    # Two founders to start. The first steps down, leaving one.
    founder = data.backup_owner_id
    stepped = await _change(supabase_engine, data, founder, founder, "admin")
    assert not isinstance(stepped, Exception), stepped
    assert await _active_owner_count(supabase_engine, data, data.owner_id) == 1

    refused = await _change(supabase_engine, data, data.owner_id, data.owner_id, "admin")
    assert isinstance(refused, WorkspaceAccessDeniedError)
    assert LAST_FOUNDER in str(refused)
    assert await _role_of(supabase_engine, data, data.owner_id) == "owner"

    # Name another founder first, and stepping down then works: ownership transfer.
    assert not isinstance(
        await _change(supabase_engine, data, data.owner_id, data.member_id, "owner"), Exception
    )
    handed_over = await _change(supabase_engine, data, data.owner_id, data.owner_id, "admin")
    assert not isinstance(handed_over, Exception), handed_over
    assert await _role_of(supabase_engine, data, data.member_id) == "owner"
    assert await _active_owner_count(supabase_engine, data, data.member_id) == 1


async def test_a_demoted_admin_immediately_loses_the_right_to_change_roles(
    supabase_engine,
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    assert not isinstance(
        await _change(supabase_engine, data, data.owner_id, data.admin_id, "member"), Exception
    )
    outcome = await _change(supabase_engine, data, data.admin_id, data.viewer_id, "member")
    assert isinstance(outcome, WorkspaceAccessDeniedError)
    assert await _role_of(supabase_engine, data, data.viewer_id) == "viewer"


async def test_two_founders_demoting_each_other_at_once_leave_exactly_one(
    owner_race_engine,
) -> None:
    data = await _seed_security_tenants(owner_race_engine)
    outcomes = await asyncio.gather(
        _change(owner_race_engine, data, data.owner_id, data.backup_owner_id, "admin"),
        _change(owner_race_engine, data, data.backup_owner_id, data.owner_id, "admin"),
    )
    succeeded = [o for o in outcomes if not isinstance(o, Exception)]
    failed = [o for o in outcomes if isinstance(o, Exception)]
    assert len(succeeded) == 1, outcomes
    assert len(failed) == 1 and isinstance(failed[0], WorkspaceAccessDeniedError), outcomes
    # Which message the loser gets depends on timing (CI found all three): the last-founder
    # guard stops it; or it was already demoted when it re-read its own role and the admin
    # ceiling applies; or the update policy already hides the founder row from it and the
    # "your role changed" check fires first. Always a denial, and a founder remains.
    assert any(m in str(failed[0]) for m in (LAST_FOUNDER, ADMIN_CEILING, ROLE_CHANGED))
    survivor = data.owner_id if not isinstance(outcomes[0], Exception) else data.backup_owner_id
    assert await _active_owner_count(owner_race_engine, data, survivor) == 1


async def test_a_caller_demoted_between_the_check_and_the_lock_is_denied_not_told_nothing_exists(
    supabase_engine,
) -> None:
    """The deterministic form of the race CI found.

    The caller is really an admin, but the permission check is made to report the stale answer
    "owner" (as it would if the winner's demotion committed just after it ran). The update
    policy then hides the founder's row from this now-admin caller, so the locked read finds
    no target. That must be a denial, never a 404, and nothing may change.
    """
    data = await _seed_security_tenants(supabase_engine)
    with patch(
        "sourcemind.api.v1.workspaces.require_workspace_permission",
        new=AsyncMock(return_value="owner"),
    ):
        outcome = await _change(supabase_engine, data, data.admin_id, data.owner_id, "member")
    assert isinstance(outcome, WorkspaceAccessDeniedError), outcome
    assert ROLE_CHANGED in str(outcome)
    assert await _role_of(supabase_engine, data, data.owner_id) == "owner"
    assert await _role_of(supabase_engine, data, data.admin_id) == "admin"
