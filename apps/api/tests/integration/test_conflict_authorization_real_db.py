"""Role gating on conflict resolution, against live Postgres.

Exercises require_workspace_role with real workspace_members rows rather than
a mocked lookup, and — the part that matters — asserts the conflict is still
unresolved in the database after a denied attempt. A gate that raises but has
already mutated state would pass a mocked test and fail here.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.core.dependencies import require_workspace_role
from sourcemind.core.exceptions import WorkspaceAccessDeniedError
from sourcemind.models.workspace import WorkspaceRole

RESOLVER_ROLES = {WorkspaceRole.OWNER.value, WorkspaceRole.ADMIN.value}


async def _member(session: AsyncSession, workspace_id, role: str) -> uuid.UUID:
    """A user with a real workspace_members row at the given role."""
    user_id = uuid.uuid4()
    suffix = uuid.uuid4().hex[:10]
    await session.execute(
        text(
            "INSERT INTO users (id, clerk_id, email, display_name) VALUES "
            "(CAST(:id AS uuid), :clerk, :email, :name)"
        ),
        {
            "id": str(user_id),
            "clerk": f"clerk-{suffix}",
            "email": f"{suffix}@t.local",
            "name": f"{role} user",
        },
    )
    await session.execute(
        text(
            "INSERT INTO workspace_members (id, workspace_id, user_id, role) VALUES "
            "(gen_random_uuid(), CAST(:ws AS uuid), CAST(:uid AS uuid), :role)"
        ),
        {"ws": str(workspace_id), "uid": str(user_id), "role": role},
    )
    await session.flush()
    return user_id


async def _memory(session: AsyncSession, workspace_id) -> uuid.UUID:
    memory_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO memories (id, workspace_id, content, content_hash, version, "
            "current_version, importance_score) VALUES "
            "(CAST(:id AS uuid), CAST(:ws AS uuid), :content, :hash, 1, TRUE, 0.5)"
        ),
        {
            "id": str(memory_id),
            "ws": str(workspace_id),
            "content": f"claim {memory_id}",
            "hash": uuid.uuid4().hex,
        },
    )
    return memory_id


async def _conflict(session: AsyncSession, workspace_id) -> uuid.UUID:
    a = await _memory(session, workspace_id)
    b = await _memory(session, workspace_id)
    conflict_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO memory_conflicts (id, workspace_id, memory_a_id, "
            "memory_b_id, conflict_type, status, similarity_score) VALUES "
            "(CAST(:id AS uuid), CAST(:ws AS uuid), CAST(:a AS uuid), "
            "CAST(:b AS uuid), 'contradiction', 'open', 0.9)"
        ),
        {"id": str(conflict_id), "ws": str(workspace_id), "a": str(a), "b": str(b)},
    )
    await session.flush()
    return conflict_id


async def _status_of(session: AsyncSession, conflict_id) -> str:
    return (
        await session.execute(
            text(
                "SELECT status FROM memory_conflicts WHERE id = CAST(:cid AS uuid)"
            ),
            {"cid": str(conflict_id)},
        )
    ).scalar()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_member_is_denied_and_the_conflict_stays_unresolved(
    db_session, test_workspace
):
    """A denied attempt must leave the conflict untouched.

    Asserting the raise alone would not catch a gate that fires after the
    resolution has already been applied.
    """
    member = await _member(db_session, test_workspace.id, "member")
    conflict_id = await _conflict(db_session, test_workspace.id)

    with pytest.raises(WorkspaceAccessDeniedError):
        await require_workspace_role(
            db_session, member, test_workspace.id, RESOLVER_ROLES
        )

    assert await _status_of(db_session, conflict_id) == "open", (
        "conflict must remain unresolved after a denied attempt"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_viewer_is_denied(db_session, test_workspace):
    viewer = await _member(db_session, test_workspace.id, "viewer")
    conflict_id = await _conflict(db_session, test_workspace.id)

    with pytest.raises(WorkspaceAccessDeniedError):
        await require_workspace_role(
            db_session, viewer, test_workspace.id, RESOLVER_ROLES
        )
    assert await _status_of(db_session, conflict_id) == "open"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_admin_passes_the_gate_and_can_resolve(db_session, test_workspace):
    """The same conflict a member could not touch is resolvable by an admin."""
    from sourcemind.services.conflict.resolver import resolve_conflict

    member = await _member(db_session, test_workspace.id, "member")
    admin = await _member(db_session, test_workspace.id, "admin")
    conflict_id = await _conflict(db_session, test_workspace.id)

    with pytest.raises(WorkspaceAccessDeniedError):
        await require_workspace_role(
            db_session, member, test_workspace.id, RESOLVER_ROLES
        )
    assert await _status_of(db_session, conflict_id) == "open"

    role = await require_workspace_role(
        db_session, admin, test_workspace.id, RESOLVER_ROLES
    )
    assert role == "admin"

    await resolve_conflict(
        session=db_session,
        conflict_id=conflict_id,
        resolver_id=admin,
        resolution_type="kept_a",
        resolution_note="admin decision",
    )
    assert await _status_of(db_session, conflict_id) == "resolved"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_owner_passes_the_gate(db_session, test_workspace):
    owner = await _member(db_session, test_workspace.id, "owner")
    assert (
        await require_workspace_role(
            db_session, owner, test_workspace.id, RESOLVER_ROLES
        )
        == "owner"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_user_from_another_workspace_is_denied(db_session, test_workspace):
    """Membership is per-workspace: admin elsewhere grants nothing here."""
    from sourcemind.models.organization import Organization
    from sourcemind.models.workspace import Workspace

    org = Organization(name="Other Org", slug=f"other-{uuid.uuid4().hex[:8]}", plan="free")
    db_session.add(org)
    await db_session.flush()
    other = Workspace(
        organization_id=org.id, name="Other", slug=f"other-{uuid.uuid4().hex[:8]}"
    )
    db_session.add(other)
    await db_session.flush()

    outsider = await _member(db_session, other.id, "admin")

    with pytest.raises(WorkspaceAccessDeniedError) as exc:
        await require_workspace_role(
            db_session, outsider, test_workspace.id, RESOLVER_ROLES
        )
    assert "not a member" in str(exc.value).lower()
