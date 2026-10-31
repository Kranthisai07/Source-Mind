"""Who may change whose role. Pure rules, so the whole matrix can be tested exhaustively.

Roles are stored as ``owner`` / ``admin`` / ``member`` / ``viewer`` and shown to people as
Founder / Admin / Teammate / Viewer (D-064). The rules:

* a founder (owner) may set any role on anyone, including promoting another founder, and
  may step down, but never leaves the workspace without an active founder;
* an admin may only move people between teammate and viewer. An admin cannot grant admin or
  founder, and cannot change an admin or a founder, so admin rights can never be self-granted
  or used to escalate;
* teammates and viewers cannot change roles at all.

The database already stops anyone but an owner from writing or touching an ``owner`` row and
stops the last owner being removed (migration 20260909_0007), but it deliberately allows an
admin to write an ``admin`` row; that is why the admin ceiling lives here, in application code.
"""

from __future__ import annotations

from sourcemind.core.exceptions import WorkspaceAccessDeniedError
from sourcemind.models.workspace import WorkspaceRole

ORDINARY_ROLES = frozenset({WorkspaceRole.MEMBER.value, WorkspaceRole.VIEWER.value})
# Roles that may add and edit memories. Losing every one of these stops queued ingestion.
CONTRIBUTE_ROLES = frozenset(
    {WorkspaceRole.OWNER.value, WorkspaceRole.ADMIN.value, WorkspaceRole.MEMBER.value}
)

LAST_FOUNDER = (
    "The last active founder cannot be demoted. Make another founder an owner first."
)
ADMIN_CEILING = (
    "Admins can only move people between teammate and viewer. "
    "A founder is needed to grant admin or founder, or to change an admin or a founder."
)
ADMIN_REQUIRED = "Administrator access is required."
ROLE_CHANGED = "Your role changed while this request was in flight. Reload and try again."


def authorize_role_change(
    *, caller_role: str, target_role: str, new_role: str, active_owner_count: int
) -> None:
    """Return normally if the change is allowed, otherwise raise ``WorkspaceAccessDeniedError``.

    ``active_owner_count`` is the number of active owners, counted under the row locks the
    caller holds (so two founders demoting each other at once cannot both pass).
    """
    if caller_role == WorkspaceRole.OWNER.value:
        if (
            target_role == WorkspaceRole.OWNER.value
            and new_role != WorkspaceRole.OWNER.value
            and active_owner_count <= 1
        ):
            raise WorkspaceAccessDeniedError(LAST_FOUNDER)
        return
    if caller_role == WorkspaceRole.ADMIN.value:
        if target_role in ORDINARY_ROLES and new_role in ORDINARY_ROLES:
            return
        raise WorkspaceAccessDeniedError(ADMIN_CEILING)
    raise WorkspaceAccessDeniedError(ADMIN_REQUIRED)


def loses_contribution(target_role: str, new_role: str) -> bool:
    """True if the change takes away the right to add and edit memories."""
    return target_role in CONTRIBUTE_ROLES and new_role not in CONTRIBUTE_ROLES
