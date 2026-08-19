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
from sourcemind.core.exceptions import WorkspaceAccessDeniedError
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
    """Someone with no membership row at all must be refused, not defaulted."""
    with pytest.raises(WorkspaceAccessDeniedError) as exc:
        await require_workspace_role(
            _session_with_role(None), uuid.uuid4(), uuid.uuid4(), RESOLVER_ROLES
        )
    assert "not a member" in str(exc.value).lower()


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


# ─── the error is a 403 with the established SM code ─────────────────────────

@pytest.mark.unit
def test_denial_maps_to_a_403_with_the_existing_error_code():
    """Reuses WorkspaceAccessDeniedError rather than inventing a new code."""
    from http import HTTPStatus

    assert WorkspaceAccessDeniedError.http_status == HTTPStatus.FORBIDDEN
    assert WorkspaceAccessDeniedError.code.value == "SM005"


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
