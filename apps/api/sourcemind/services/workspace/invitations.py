"""Workspace invitations (D-065): create, list, revoke and accept.

The token is 256 bits of randomness, shown once and stored only as a SHA-256 hash, so a database
read cannot reveal a usable link. Accepting needs the token AND a Clerk-verified primary email
equal to the invited one; every other reason an invitation cannot be used (unknown, expired,
used, revoked, the inviter lost their authority, wrong person) looks identical to the caller, so
the endpoint is not an oracle for any of them.

Who may invite whom mirrors role management (roles.py): a founder may invite as any role, an admin
only as teammate or viewer. The database enforces the same ceiling on insert, and re-checks at
acceptance that the inviter still holds the authority the invited role needs.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.core.exceptions import (
    InvitationNotFoundError,
    ValidationError,
    WorkspaceAccessDeniedError,
    WorkspaceMemberExistsError,
)
from sourcemind.models.user import User
from sourcemind.models.workspace import WorkspaceMember, WorkspaceMembershipStatus, WorkspaceRole
from sourcemind.models.workspace_invitation import WorkspaceInvitation
from sourcemind.services.workspace.roles import ADMIN_REQUIRED, ORDINARY_ROLES

logger = structlog.get_logger(__name__)

TOKEN_PREFIX = "smi_"  # noqa: S105  (a format prefix, not a secret)
INVITATION_TTL = timedelta(days=7)
MAX_PENDING_PER_WORKSPACE = 50
FOUNDER_ONLY = "Only a founder can invite someone as admin or founder."
_RLS_DENIAL = "42501"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_token() -> tuple[str, str]:
    """A new token and its hash. The token is returned to the inviter once and not kept."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    return token, hash_token(token)


def authorize_invitation(*, caller_role: str, invite_role: str) -> None:
    """Raise unless ``caller_role`` may invite, or revoke an invitation, as ``invite_role``."""
    if caller_role == WorkspaceRole.OWNER.value:
        return
    if caller_role == WorkspaceRole.ADMIN.value:
        if invite_role in ORDINARY_ROLES:
            return
        raise WorkspaceAccessDeniedError(FOUNDER_ONLY)
    raise WorkspaceAccessDeniedError(ADMIN_REQUIRED)


def _pending(now: datetime):
    return (
        WorkspaceInvitation.accepted_at.is_(None),
        WorkspaceInvitation.revoked_at.is_(None),
        WorkspaceInvitation.expires_at > now,
    )


async def create_invitation(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    caller_id: uuid.UUID,
    caller_role: str,
    email: str,
    role: str,
) -> tuple[WorkspaceInvitation, str]:
    """Create an invitation and return it with the one-time token. Commits."""
    authorize_invitation(caller_role=caller_role, invite_role=role)
    now = datetime.now(UTC)

    already_member = await db.scalar(
        select(func.count())
        .select_from(WorkspaceMember)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.status == WorkspaceMembershipStatus.ACTIVE,
            WorkspaceMember.departed_at.is_(None),
            func.lower(User.email) == email,
        )
    )
    if already_member:
        raise WorkspaceMemberExistsError(
            "That person is already a member of this workspace.",
            details={"reason": "already_member"},
        )

    pending = await db.scalar(
        select(func.count())
        .select_from(WorkspaceInvitation)
        .where(WorkspaceInvitation.workspace_id == workspace_id, *_pending(now))
    )
    if int(pending or 0) >= MAX_PENDING_PER_WORKSPACE:
        raise ValidationError(
            "This workspace has too many pending invitations. Revoke some first.",
            details={"reason": "too_many_pending"},
        )

    # One live invitation per person: a new one replaces the old. The revoke policy lets an
    # admin supersede only teammate/viewer invitations, so a founder's pending invitation to the
    # same address survives and the insert below is refused instead.
    await db.execute(
        update(WorkspaceInvitation)
        .where(
            WorkspaceInvitation.workspace_id == workspace_id,
            WorkspaceInvitation.email == email,
            WorkspaceInvitation.accepted_at.is_(None),
            WorkspaceInvitation.revoked_at.is_(None),
        )
        .values(revoked_at=now, revoked_by=caller_id)
    )

    token, digest = generate_token()
    invitation = WorkspaceInvitation(
        workspace_id=workspace_id,
        email=email,
        role=role,
        token_hash=digest,
        invited_by=caller_id,
        created_at=now,
        expires_at=now + INVITATION_TTL,
    )
    db.add(invitation)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ValidationError(
            "An invitation for this address is already pending and was created by a founder.",
            details={"reason": "pending_exists"},
        ) from exc
    except DBAPIError as exc:
        await db.rollback()
        if _is_rls_denial(exc):
            raise WorkspaceAccessDeniedError(FOUNDER_ONLY) from exc
        raise
    await db.commit()
    logger.info(
        "workspace.invitation_created",
        workspace_id=str(workspace_id),
        invitation_id=str(invitation.id),
        role=role,
        actor_id=str(caller_id),
    )
    return invitation, token


async def list_pending_invitations(
    db: AsyncSession, *, workspace_id: uuid.UUID
) -> list[WorkspaceInvitation]:
    rows = await db.execute(
        select(WorkspaceInvitation)
        .where(WorkspaceInvitation.workspace_id == workspace_id, *_pending(datetime.now(UTC)))
        .order_by(WorkspaceInvitation.created_at.desc(), WorkspaceInvitation.id)
    )
    return list(rows.scalars().all())


async def revoke_invitation(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    invitation_id: uuid.UUID,
    caller_id: uuid.UUID,
    caller_role: str,
) -> None:
    """Revoke a pending invitation: admins teammate/viewer ones, founders any. Commits."""
    now = datetime.now(UTC)
    still_pending = (
        WorkspaceInvitation.id == invitation_id,
        WorkspaceInvitation.workspace_id == workspace_id,
        WorkspaceInvitation.accepted_at.is_(None),
        WorkspaceInvitation.revoked_at.is_(None),
    )
    # Read first, without a lock, so an admin pointing at a founder-level invitation gets a clear
    # refusal: the revoke policy hides those rows from admins, so they cannot be locked at all.
    invited_role = await db.scalar(select(WorkspaceInvitation.role).where(*still_pending))
    if invited_role is None:
        raise InvitationNotFoundError("Invitation not found.")
    authorize_invitation(caller_role=caller_role, invite_role=invited_role)
    invitation = (
        await db.execute(select(WorkspaceInvitation).where(*still_pending).with_for_update())
    ).scalar_one_or_none()
    if invitation is None:  # finished or revoked by someone else in the meantime
        raise InvitationNotFoundError("Invitation not found.")
    invitation.revoked_at = now
    invitation.revoked_by = caller_id
    try:
        await db.flush()
    except DBAPIError as exc:
        await db.rollback()
        if _is_rls_denial(exc):
            raise WorkspaceAccessDeniedError(FOUNDER_ONLY) from exc
        raise
    await db.commit()
    logger.info(
        "workspace.invitation_revoked",
        workspace_id=str(workspace_id),
        invitation_id=str(invitation_id),
        actor_id=str(caller_id),
    )


async def accept_invitation(
    db: AsyncSession, *, user_id: uuid.UUID, verified_email: str, token: str
) -> tuple[uuid.UUID, str]:
    """Join the workspace the token names. Returns (workspace_id, role). Commits.

    ``verified_email`` must already be the user's Clerk-verified primary email. The token hash and
    that email are handed to the database as transaction-local settings, which is what unlocks
    exactly the one live invitation and the one membership row the policies permit.
    """
    now = datetime.now(UTC)
    digest = hash_token(token)
    await db.execute(
        text(
            "SELECT set_config('app.invitation_token_hash', CAST(:h AS text), true), "
            "set_config('app.invitation_email', CAST(:e AS text), true)"
        ),
        {"h": digest, "e": verified_email.lower()},
    )
    try:
        invitation = (
            await db.execute(
                select(WorkspaceInvitation)
                .where(WorkspaceInvitation.token_hash == digest)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if invitation is None or invitation.email != verified_email.lower():
            raise InvitationNotFoundError()

        try:
            async with db.begin_nested():
                db.add(
                    WorkspaceMember(
                        workspace_id=invitation.workspace_id,
                        user_id=user_id,
                        role=invitation.role,
                        status=WorkspaceMembershipStatus.ACTIVE.value,
                        invited_by_id=invitation.invited_by,
                    )
                )
                await db.flush()
        except IntegrityError:
            # A row for this person already exists. Only a departed one may be brought back; an
            # active member or one mid-handoff must not be touched by an invitation.
            reactivated = (
                await db.execute(
                    update(WorkspaceMember)
                    .where(
                        WorkspaceMember.workspace_id == invitation.workspace_id,
                        WorkspaceMember.user_id == user_id,
                        WorkspaceMember.status == WorkspaceMembershipStatus.DEPARTED.value,
                    )
                    .values(
                        role=invitation.role,
                        status=WorkspaceMembershipStatus.ACTIVE.value,
                        departed_at=None,
                        invited_by_id=invitation.invited_by,
                    )
                    .returning(WorkspaceMember.id)
                )
            ).first()
            if reactivated is None:
                raise InvitationNotFoundError() from None

        workspace_id, role = invitation.workspace_id, invitation.role
        invitation.accepted_at = now
        invitation.accepted_by = user_id
        await db.flush()
        await db.commit()
    except InvitationNotFoundError:
        await db.rollback()
        raise
    except DBAPIError as exc:
        await db.rollback()
        if _is_rls_denial(exc) or "row-level security" in str(exc):
            # The inviter lost authority, the role is no longer grantable, or the row was not
            # ours to create. Indistinguishable from any other invalid invitation, on purpose.
            raise InvitationNotFoundError() from exc
        raise
    logger.info(
        "workspace.invitation_accepted",
        workspace_id=str(workspace_id),
        user_id=str(user_id),
        role=role,
    )
    return workspace_id, role


def _is_rls_denial(exc: DBAPIError) -> bool:
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return sqlstate == _RLS_DENIAL or "row-level security" in str(exc)
