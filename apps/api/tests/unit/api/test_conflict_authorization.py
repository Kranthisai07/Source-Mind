"""Only owners and admins may resolve a conflict.

Resolving retires memories and can create a merged one, so it is the only
conflict action that is gated. Listing, viewing detail and marking a conflict
under review stay open to any workspace member — a reviewer must be able to
triage without being able to decide.

require_workspace_role is the first role gate in the codebase; there was no
prior pattern to follow. workspaces.py queries WorkspaceMember.role to FIND
an owner rather than to authorize, and AuthenticatedUser.workspace_role is
declared but never assigned.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from sourcemind.core.dependencies import require_workspace_role
from sourcemind.core.exceptions import (
    WorkspaceAccessDeniedError,
    WorkspaceNotFoundError,
)
from sourcemind.models.workspace import WorkspaceRole

RESOLVER_ROLES = {WorkspaceRole.OWNER.value, WorkspaceRole.ADMIN.value}


def _session_with_role(role: str | None):
    """Session whose membership lookup returns `role`, or no row for None."""
    session = AsyncMock()
    result = MagicMock()
    result.first = MagicMock(
        return_value=MagicMock(role=role) if role is not None else None
    )
    session.execute = AsyncMock(return_value=result)
    return session


# ─── denied ──────────────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_conflict_denied_for_member_role():
    with pytest.raises(WorkspaceAccessDeniedError) as exc:
        await require_workspace_role(
            _session_with_role("member"), uuid.uuid4(), uuid.uuid4(), RESOLVER_ROLES
        )
    assert "member" in str(exc.value), "the error should name the caller's role"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_conflict_denied_for_viewer_role():
    with pytest.raises(WorkspaceAccessDeniedError):
        await require_workspace_role(
            _session_with_role("viewer"), uuid.uuid4(), uuid.uuid4(), RESOLVER_ROLES
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_conflict_denied_for_non_member():
    """A non-member gets 404, not 403.

    This asserted WorkspaceAccessDeniedError until a live isolation audit
    showed the 403 was itself a disclosure: it confirmed to an unauthorized
    caller that the workspace id they named is real. The two failure modes are
    now deliberately different - see the pair of tests below.
    """
    with pytest.raises(WorkspaceNotFoundError):
        await require_workspace_role(
            _session_with_role(None), uuid.uuid4(), uuid.uuid4(), RESOLVER_ROLES
        )


# ─── allowed ─────────────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_conflict_allowed_for_admin_role():
    role = await require_workspace_role(
        _session_with_role("admin"), uuid.uuid4(), uuid.uuid4(), RESOLVER_ROLES
    )
    assert role == "admin"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_conflict_allowed_for_owner_role():
    role = await require_workspace_role(
        _session_with_role("owner"), uuid.uuid4(), uuid.uuid4(), RESOLVER_ROLES
    )
    assert role == "owner"


# ─── the two failure modes are distinct, and that distinction is the point ───

@pytest.mark.unit
def test_insufficient_role_maps_to_a_403_with_the_existing_error_code():
    """A MEMBER holding the wrong role gets 403.

    They already know the workspace exists, so 403 discloses nothing further
    and is the more accurate answer.
    """
    from http import HTTPStatus

    assert WorkspaceAccessDeniedError.http_status == HTTPStatus.FORBIDDEN
    assert WorkspaceAccessDeniedError.code.value == "SM005"


@pytest.mark.unit
def test_non_membership_maps_to_a_404_so_existence_is_not_confirmed():
    """A NON-member gets 404. A 403 here would leak that the workspace exists."""
    from http import HTTPStatus

    assert WorkspaceNotFoundError.http_status == HTTPStatus.NOT_FOUND
    assert WorkspaceNotFoundError.code.value == "SM022"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_wrong_role_member_and_a_non_member_are_told_different_things():
    """The gate must not collapse these two cases back into one answer."""
    with pytest.raises(WorkspaceAccessDeniedError):
        await require_workspace_role(
            _session_with_role("viewer"), uuid.uuid4(), uuid.uuid4(), RESOLVER_ROLES
        )
    with pytest.raises(WorkspaceNotFoundError):
        await require_workspace_role(
            _session_with_role(None), uuid.uuid4(), uuid.uuid4(), RESOLVER_ROLES
        )


# ─── scope: only resolve is gated ────────────────────────────────────────────

@pytest.mark.unit
def test_only_the_resolve_endpoint_is_gated():
    """List, detail and review must remain open to any member.

    Triage and decision are different actions; gating review as well would
    stop a member from even flagging a conflict for attention.
    """
    import ast
    import pathlib

    src = (
        pathlib.Path(__file__).resolve().parents[3]
        / "sourcemind"
        / "api"
        / "v1"
        / "conflicts.py"
    )
    tree = ast.parse(src.read_text(encoding="utf-8"))

    gated = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(sub, ast.Name) and sub.id == "require_workspace_role"
            for sub in ast.walk(node)
        )
    }
    assert gated == {"resolve_conflict_endpoint"}, (
        f"exactly one endpoint should be gated, found: {sorted(gated)}"
    )
