"""
Workspace resource endpoints.

GET  /v1/workspaces              → list workspaces for current user
POST /v1/workspaces              → create workspace (auto-creates org if needed)
GET  /v1/workspaces/:id          → get workspace details
GET  /v1/workspaces/:id/members  → list members with roles
POST /v1/workspaces/:id/members  → owner/admin adds an ordinary member by email
PATCH /v1/workspaces/:id/members/:user_id → change a role (founder: any; admin: teammate/viewer)
GET  /v1/workspaces/:id/analytics → contribution map + knowledge gaps
"""

import time
import uuid
from datetime import UTC, datetime
from uuid import UUID

import structlog
from fastapi import APIRouter, status
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from sourcemind.core.config import get_settings
from sourcemind.core.database import set_rls_workspace_context
from sourcemind.core.dependencies import (
    CurrentUser,
    DBSession,
    IdempotencyKey,
    RequestID,
    WorkspacePermission,
    fetch_clerk_primary_email_verification,
    require_workspace_member,
    require_workspace_permission,
)
from sourcemind.core.exceptions import (
    ServiceUnavailableError,
    UserNotFoundError,
    ValidationError,
    WorkspaceAccessDeniedError,
    WorkspaceMemberExistsError,
    WorkspaceNotFoundError,
)
from sourcemind.core.rate_limit import RateLimitedOperation, enforce_rate_limit
from sourcemind.models.organization import Organization
from sourcemind.models.user import User
from sourcemind.models.workspace import (
    Workspace,
    WorkspaceMember,
    WorkspaceMembershipStatus,
    WorkspaceRole,
)
from sourcemind.schemas.common import APIResponse, ResponseMeta
from sourcemind.schemas.user import UserSummary
from sourcemind.schemas.workspace import (
    WorkspaceCreate,
    WorkspaceMemberAdd,
    WorkspaceMemberResponse,
    WorkspaceMemberRoleUpdate,
    WorkspaceResponse,
)
from sourcemind.services.ingestion.lifecycle import (
    REVOCATION_INGESTION_ERROR,
    ROLE_CHANGE_INGESTION_ERROR,
    terminalize_member_ingestion_documents,
)
from sourcemind.services.workspace.roles import (
    ADMIN_CEILING,
    ADMIN_REQUIRED,
    LAST_FOUNDER,
    authorize_role_change,
    loses_contribution,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


def _make_meta(request_id: str, start: float) -> ResponseMeta:
    return ResponseMeta(
        request_id=request_id,
        timestamp=datetime.now(UTC),
        latency_ms=(time.perf_counter() - start) * 1000,
    )


@router.get(
    "",
    response_model=APIResponse[list[WorkspaceResponse]],
    summary="List workspaces for current user",
)
async def list_workspaces(
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
) -> APIResponse[list[WorkspaceResponse]]:
    """Return all workspaces the current user is a member of."""
    start = time.perf_counter()

    result = await db.execute(
        select(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(
            WorkspaceMember.user_id == current_user.user_id,
            WorkspaceMember.status == WorkspaceMembershipStatus.ACTIVE,
            WorkspaceMember.departed_at.is_(None),
            Workspace.deleted_at.is_(None),
        )
        .order_by(Workspace.created_at.asc())
    )
    workspaces = result.scalars().all()

    return APIResponse(
        data=[WorkspaceResponse.model_validate(ws) for ws in workspaces],
        meta=_make_meta(request_id, start),
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=APIResponse[WorkspaceResponse],
    summary="Create a new workspace",
)
async def create_workspace(
    body: WorkspaceCreate,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
    idempotency_key: IdempotencyKey,
) -> APIResponse[WorkspaceResponse]:
    """
    Create a workspace. If the user has no organization, one is auto-created.
    The creating user becomes the workspace owner.
    """
    start = time.perf_counter()

    await enforce_rate_limit(
        RateLimitedOperation.WORKSPACE_CREATE, current_user.user_id
    )

    # Find or auto-create an organization for this user
    # Look for an org where the user already owns a workspace
    existing_org_result = await db.execute(
        select(Organization)
        .join(Workspace, Workspace.organization_id == Organization.id)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(
            WorkspaceMember.user_id == current_user.user_id,
            WorkspaceMember.role == WorkspaceRole.OWNER,
            WorkspaceMember.status == WorkspaceMembershipStatus.ACTIVE,
            WorkspaceMember.departed_at.is_(None),
            Organization.deleted_at.is_(None),
        )
        .limit(1)
    )
    org = existing_org_result.scalar_one_or_none()

    if org is None:
        # Auto-create a personal organization using the user's email prefix as slug
        email_prefix = current_user.email.split("@")[0]
        org_slug = f"{email_prefix}-org"
        # Ensure slug uniqueness
        collision = await db.execute(
            select(Organization).where(Organization.slug == org_slug)
        )
        if collision.scalar_one_or_none():
            org_slug = f"{email_prefix}-{str(uuid.uuid4())[:8]}"

        org = Organization(
            name=f"{current_user.display_name or email_prefix}'s Organization",
            slug=org_slug,
        )
        db.add(org)
        await db.flush()
        logger.info("workspace.org_auto_created", org_id=str(org.id), slug=org_slug)

    # Create the workspace
    workspace_id = uuid.uuid4()
    await set_rls_workspace_context(db, workspace_id)
    workspace = Workspace(
        id=workspace_id,
        organization_id=org.id,
        created_by_user_id=current_user.user_id,
        name=body.name,
        slug=body.slug,
        description=body.description,
    )
    db.add(workspace)
    await db.flush()

    # Add creator as owner
    membership = WorkspaceMember(
        workspace_id=workspace.id,
        user_id=current_user.user_id,
        role=WorkspaceRole.OWNER,
    )
    db.add(membership)
    await db.commit()
    await db.refresh(workspace)

    logger.info(
        "workspace.created",
        workspace_id=str(workspace.id),
        slug=workspace.slug,
        user_id=str(current_user.user_id),
    )

    return APIResponse(
        data=WorkspaceResponse.model_validate(workspace),
        meta=_make_meta(request_id, start),
    )


@router.get(
    "/{workspace_id}",
    response_model=APIResponse[WorkspaceResponse],
    summary="Get workspace details",
)
async def get_workspace(
    workspace_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
) -> APIResponse[WorkspaceResponse]:
    """Retrieve workspace details. User must be a member."""
    start = time.perf_counter()

    # The membership JOIN below already enforced this correctly, but it did so
    # implicitly - an audit of "which routes check membership" could not see it
    # without reading the query. Calling the shared gate makes the check
    # explicit and greppable; the JOIN stays because it also filters
    # deleted_at, which membership alone does not.
    await require_workspace_member(db, current_user.user_id, workspace_id)

    result = await db.execute(
        select(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(
            Workspace.id == workspace_id,
            WorkspaceMember.user_id == current_user.user_id,
            WorkspaceMember.status == WorkspaceMembershipStatus.ACTIVE,
            WorkspaceMember.departed_at.is_(None),
            Workspace.deleted_at.is_(None),
        )
    )
    workspace = result.scalar_one_or_none()
    if workspace is None:
        raise WorkspaceNotFoundError(str(workspace_id))

    return APIResponse(
        data=WorkspaceResponse.model_validate(workspace),
        meta=_make_meta(request_id, start),
    )


@router.get(
    "/{workspace_id}/members",
    response_model=APIResponse[list[WorkspaceMemberResponse]],
    summary="List workspace members",
)
async def list_workspace_members(
    workspace_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
) -> APIResponse[list[WorkspaceMemberResponse]]:
    """List all members of a workspace with their roles."""
    start = time.perf_counter()

    # Caller must be a member. This handler's own inline check was the pattern
    # require_workspace_member was extracted from; it now calls the shared one
    # so there is a single place this behaviour is defined.
    await require_workspace_member(db, current_user.user_id, workspace_id)

    result = await db.execute(
        select(WorkspaceMember, User)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.status == WorkspaceMembershipStatus.ACTIVE,
            WorkspaceMember.departed_at.is_(None),
        )
        .order_by(WorkspaceMember.created_at.asc())
    )
    members = [
        WorkspaceMemberResponse(
            user=UserSummary.model_validate(user),
            role=WorkspaceRole(member.role),
            joined_at=member.created_at,
        )
        for member, user in result.all()
    ]

    return APIResponse(data=members, meta=_make_meta(request_id, start))


_RLS_INSUFFICIENT_PRIVILEGE = "42501"
_UNIQUE_MEMBERSHIP = "uq_workspace_members_workspace_user"
# deadlock_detected, serialization_failure
_RETRYABLE_SQLSTATES = frozenset({"40P01", "40001"})


def _pg_sqlstate(exc: DBAPIError) -> str | None:
    orig = getattr(exc, "orig", None)
    return getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)


def _is_rls_denial(exc: DBAPIError) -> bool:
    """True for an RLS WITH CHECK / policy denial (SQLSTATE 42501)."""
    return _pg_sqlstate(exc) == _RLS_INSUFFICIENT_PRIVILEGE or "row-level security" in str(exc)


@router.post(
    "/{workspace_id}/members",
    status_code=status.HTTP_201_CREATED,
    response_model=APIResponse[WorkspaceMemberResponse],
    summary="Add an existing user to a workspace by email",
)
async def add_workspace_member(
    workspace_id: UUID,
    body: WorkspaceMemberAdd,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
    idempotency_key: IdempotencyKey,
) -> APIResponse[WorkspaceMemberResponse]:
    """
    Add a user who has already signed in to this workspace as member/viewer.

    Notes recorded in D-024:
    - Idempotency-Key is validated but NOT de-duplicated. Idempotency here is
      by natural key (workspace_id, user_id): a repeated add returns 409
      already_member rather than a second row.
    - The endpoint reveals whether an email belongs to a signed-in user, but
      only to this workspace's admins/owners and under a per-user rate limit.
    - A returning (departed) member's attribution history and GitHub author
      links reactivate with the membership; ingestion documents that were
      failed at revocation stay failed.
    - joined_at is the membership row's created_at (first join), not the time
      of a re-add.
    """
    start = time.perf_counter()
    requested_email = body.email.lower()
    caller_id = current_user.user_id

    await enforce_rate_limit(RateLimitedOperation.MEMBER_ADD, caller_id)
    await require_workspace_permission(
        db,
        caller_id,
        workspace_id,
        WorkspacePermission.ADMINISTER,
    )
    # Workspace-wide ceiling on top of the per-user one (the counter is keyed by
    # the workspace id, passed as both scope and subject), so admins of one
    # workspace cannot multiply the probe/Clerk budget by taking turns. It sits
    # AFTER the ADMINISTER check on purpose: the bucket is shared, so only an
    # authorized administrator may spend it. Otherwise any signed-in user who
    # knows a workspace id could exhaust it and lock the real admins out. (A
    # retried request consumes a token from both limiters.)
    await enforce_rate_limit(
        RateLimitedOperation.MEMBER_ADD_WORKSPACE, workspace_id, workspace_id
    )

    def _conflict(existing_status: str | None) -> WorkspaceMemberExistsError:
        if existing_status == WorkspaceMembershipStatus.DEPARTING.value:
            return WorkspaceMemberExistsError(
                "A handoff for this member is in progress.",
                details={"reason": "handoff_in_progress"},
            )
        return WorkspaceMemberExistsError(
            "That user is already a member of this workspace.",
            details={"reason": "already_member"},
        )

    # Resolve the target. Case-insensitive; two matches are never disambiguated.
    # This happens BEFORE any membership row is locked so the lock order below
    # can be decided from both user ids.
    candidates = (
        (
            await db.execute(
                select(User)
                .where(func.lower(User.email) == requested_email, User.deleted_at.is_(None))
                .limit(2)
            )
        )
        .scalars()
        .all()
    )
    if not candidates:
        raise UserNotFoundError(
            "No SourceMind user has that email. The person must sign in to "
            "SourceMind once before they can be added.",
            details={"reason": "user_not_found"},
        )
    if len(candidates) > 1:
        raise ValidationError(
            "More than one account matches that email, so it cannot be used.",
            details={"reason": "email_ambiguous"},
        )
    target = candidates[0]
    target_id = target.id

    # An active administrator adding themselves is an already-existing member by
    # definition; refuse without locking or asking Clerk (and so without ever
    # needing a share->exclusive upgrade on one row).
    if target_id == caller_id:
        raise _conflict(WorkspaceMembershipStatus.ACTIVE.value)

    # Trust the association only if Clerk (server-side, uncached) confirms the
    # stored user's PRIMARY email is verified and equals stored and requested
    # email. With the explicit development bypass there is no Clerk to ask;
    # that flag is rejected by Settings outside development.
    settings = get_settings()
    if not settings.auth_dev_bypass_enabled:
        clerk_email, verification = await fetch_clerk_primary_email_verification(
            target.clerk_id, settings.clerk_secret_key
        )
        if not (
            verification == "verified"
            and clerk_email.lower() == str(target.email).lower() == requested_email
        ):
            raise ValidationError(
                "That email is not a verified primary email for the account.",
                details={"reason": "email_not_verified"},
            )

    former_owner_denied = WorkspaceAccessDeniedError(
        "Only an owner can re-add a former owner."
    )
    reactivated = False
    try:
        # Lock the two membership rows (caller: FOR SHARE, target: FOR UPDATE) in
        # ONE global order - ascending user id - so two requests can never each
        # hold the row the other wants (A adds B while B adds A). The caller lock
        # makes a concurrent revoke of the caller wait for this request, or, if
        # the revoke already committed, makes this request refuse.
        caller_role = ""
        existing = None
        for user_id in sorted({caller_id, target_id}, key=str):
            if user_id == caller_id:
                caller = (
                    await db.execute(
                        select(WorkspaceMember.role, WorkspaceMember.status)
                        .where(
                            WorkspaceMember.workspace_id == workspace_id,
                            WorkspaceMember.user_id == caller_id,
                        )
                        .with_for_update(read=True)
                    )
                ).first()
                if caller is None or caller.status != WorkspaceMembershipStatus.ACTIVE.value:
                    raise WorkspaceNotFoundError(str(workspace_id))
                if caller.role not in (WorkspaceRole.ADMIN.value, WorkspaceRole.OWNER.value):
                    raise WorkspaceAccessDeniedError("Administrator access is required.")
                caller_role = caller.role
            else:
                existing = (
                    await db.execute(
                        select(
                            WorkspaceMember.id,
                            WorkspaceMember.role,
                            WorkspaceMember.status,
                        )
                        .where(
                            WorkspaceMember.workspace_id == workspace_id,
                            WorkspaceMember.user_id == target_id,
                        )
                        .with_for_update()
                    )
                ).first()
        caller_is_owner = caller_role == WorkspaceRole.OWNER.value

        if existing is None:
            savepoint = await db.begin_nested()
            try:
                db.add(
                    WorkspaceMember(
                        workspace_id=workspace_id,
                        user_id=target_id,
                        role=body.role,
                        status=WorkspaceMembershipStatus.ACTIVE.value,
                        invited_by_id=caller_id,
                    )
                )
                await db.flush()
                await savepoint.commit()
            except IntegrityError as exc:
                await savepoint.rollback()
                if _UNIQUE_MEMBERSHIP not in str(getattr(exc, "orig", exc)):
                    # Some other integrity failure (e.g. a foreign key): not a
                    # duplicate membership, so do not report it as one.
                    raise
                # Lost a race (or the row is invisible to FOR UPDATE because RLS
                # filters a former-owner row for a non-owner). Classify the winner.
                winner = (
                    await db.execute(
                        select(WorkspaceMember.role, WorkspaceMember.status).where(
                            WorkspaceMember.workspace_id == workspace_id,
                            WorkspaceMember.user_id == target_id,
                        )
                    )
                ).first()
                if (
                    winner is not None
                    and winner.status == WorkspaceMembershipStatus.DEPARTED.value
                    and winner.role == WorkspaceRole.OWNER.value
                    and not caller_is_owner
                ):
                    raise former_owner_denied from exc
                raise _conflict(winner.status if winner is not None else None) from exc
            except DBAPIError as exc:
                await savepoint.rollback()
                if _is_rls_denial(exc):
                    raise WorkspaceAccessDeniedError(
                        "Administrator access is required."
                    ) from exc
                raise
        elif existing.status in (
            WorkspaceMembershipStatus.ACTIVE.value,
            WorkspaceMembershipStatus.DEPARTING.value,
        ):
            raise _conflict(existing.status)
        else:
            # departed: reactivate with the REQUESTED role. The old role (possibly
            # admin or owner) is never restored.
            try:
                updated = (
                    await db.execute(
                        update(WorkspaceMember)
                        .where(
                            WorkspaceMember.id == existing.id,
                            WorkspaceMember.status
                            == WorkspaceMembershipStatus.DEPARTED.value,
                        )
                        .values(
                            role=body.role,
                            status=WorkspaceMembershipStatus.ACTIVE.value,
                            departed_at=None,
                            invited_by_id=caller_id,
                        )
                        .returning(WorkspaceMember.id)
                    )
                ).first()
            except DBAPIError as exc:
                if _is_rls_denial(exc):
                    raise WorkspaceAccessDeniedError(
                        "Administrator access is required."
                    ) from exc
                raise
            if updated is None:
                # RLS USING filtered the row (former owner, non-owner caller) or
                # it changed under us. Never a silent no-op.
                if existing.role == WorkspaceRole.OWNER.value and not caller_is_owner:
                    raise former_owner_denied
                raise _conflict(None)
            reactivated = True

        stored = (
            await db.execute(
                select(
                    WorkspaceMember.role,
                    WorkspaceMember.status,
                    WorkspaceMember.departed_at,
                    WorkspaceMember.created_at,
                ).where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.user_id == target_id,
                )
            )
        ).first()
        if (
            stored is None
            or stored.role != body.role
            or stored.status != WorkspaceMembershipStatus.ACTIVE.value
            or stored.departed_at is not None
        ):
            # Not reachable unless a policy or trigger changed the write.
            raise ServiceUnavailableError("Membership could not be confirmed.")

        response = WorkspaceMemberResponse(
            user=UserSummary.model_validate(target),
            role=WorkspaceRole(stored.role),
            joined_at=stored.created_at,
        )
        await db.commit()
    except DBAPIError as exc:
        if _pg_sqlstate(exc) in _RETRYABLE_SQLSTATES or "deadlock detected" in str(exc):
            # Defence in depth behind the ordered locking above: a deadlock or
            # serialization abort is a clean, retryable 503, never a 500.
            await db.rollback()
            log = logger.bind(workspace_id=str(workspace_id), actor_id=str(caller_id))
            log.warning("workspace.member_add_retryable_conflict", sqlstate=_pg_sqlstate(exc))
            raise ServiceUnavailableError(
                "The membership changed concurrently. Please retry.",
                details={"reason": "concurrent_update_retry"},
            ) from exc
        raise

    logger.info(
        "workspace.member_added",
        workspace_id=str(workspace_id),
        target_user_id=str(target_id),
        role=body.role,
        actor_id=str(caller_id),
        reactivated=reactivated,
    )
    return APIResponse(data=response, meta=_make_meta(request_id, start))


@router.delete(
    "/{workspace_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a workspace membership",
)
async def revoke_workspace_member(
    workspace_id: UUID,
    user_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
    idempotency_key: IdempotencyKey,
) -> None:
    """Soft-revoke an active member without deleting audit history."""
    caller_role = await require_workspace_permission(
        db,
        current_user.user_id,
        workspace_id,
        WorkspacePermission.ADMINISTER,
    )
    result = await db.execute(
        select(WorkspaceMember)
        .where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
            WorkspaceMember.status == WorkspaceMembershipStatus.ACTIVE,
            WorkspaceMember.departed_at.is_(None),
        )
        .with_for_update()
    )
    membership = result.scalar_one_or_none()
    if membership is None:
        raise UserNotFoundError("Active workspace member not found.")

    if membership.role == WorkspaceRole.OWNER:
        if caller_role != WorkspaceRole.OWNER.value:
            raise WorkspaceAccessDeniedError("Only an owner can revoke an owner.")
        owners_result = await db.execute(
            select(WorkspaceMember.id).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.role == WorkspaceRole.OWNER,
                WorkspaceMember.status == WorkspaceMembershipStatus.ACTIVE,
                WorkspaceMember.departed_at.is_(None),
            )
        )
        if len(owners_result.scalars().all()) <= 1:
            raise WorkspaceAccessDeniedError(
                "The last active workspace owner cannot be revoked."
            )

    await terminalize_member_ingestion_documents(
        db,
        workspace_id,
        user_id,
        error_message=REVOCATION_INGESTION_ERROR,
    )

    membership.status = WorkspaceMembershipStatus.DEPARTED.value
    membership.departed_at = datetime.now(UTC)
    await db.commit()


@router.patch(
    "/{workspace_id}/members/{user_id}",
    response_model=APIResponse[WorkspaceMemberResponse],
    summary="Change a member's role",
)
async def change_workspace_member_role(
    workspace_id: UUID,
    user_id: UUID,
    body: WorkspaceMemberRoleUpdate,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
    idempotency_key: IdempotencyKey,
) -> APIResponse[WorkspaceMemberResponse]:
    """
    Change an active member's role (D-064).

    Founders (owners) may set any role on anyone and may promote other founders. Admins may
    only move people between teammate (member) and viewer; they can never grant admin or
    founder and never change an admin or a founder. The last active founder cannot be demoted.
    A change is a no-op (200, nothing written) when the member already has that role.

    Concurrency: the caller, the target and every active owner are locked in ascending
    user-id order (the order add/revoke use), and the owner count is taken from the locked
    rows, so two founders demoting each other at once cannot leave the workspace with none.
    The row-level-security policies and the last-owner trigger remain as a backstop.
    Idempotency-Key is validated, not de-duplicated; setting the same role twice is a no-op.
    """
    start = time.perf_counter()
    caller_id = current_user.user_id

    await enforce_rate_limit(RateLimitedOperation.ROLE_CHANGE, caller_id)
    caller_role = await require_workspace_permission(
        db, caller_id, workspace_id, WorkspacePermission.ADMINISTER
    )

    # Unlocked read first so an admin pointing at a founder gets a clear 403, not a 404
    # (the update policy hides owner rows from non-owners, so they cannot be locked).
    preview = (
        await db.execute(
            select(WorkspaceMember.role).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user_id,
                WorkspaceMember.status == WorkspaceMembershipStatus.ACTIVE,
                WorkspaceMember.departed_at.is_(None),
            )
        )
    ).first()
    if preview is None:
        raise UserNotFoundError("Active workspace member not found.")
    authorize_role_change(
        caller_role=caller_role,
        target_role=str(preview.role),
        new_role=body.role,
        # Cannot decide the founder count without the locks; this first pass only applies the
        # role ceilings, so assume "not the last" and let the locked pass decide.
        active_owner_count=2,
    )

    try:
        locked = (
            (
                await db.execute(
                    select(WorkspaceMember)
                    .where(
                        WorkspaceMember.workspace_id == workspace_id,
                        WorkspaceMember.status == WorkspaceMembershipStatus.ACTIVE,
                        WorkspaceMember.departed_at.is_(None),
                        or_(
                            WorkspaceMember.user_id.in_([caller_id, user_id]),
                            WorkspaceMember.role == WorkspaceRole.OWNER,
                        ),
                    )
                    .order_by(WorkspaceMember.user_id)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        by_user = {member.user_id: member for member in locked}
        caller = by_user.get(caller_id)
        target = by_user.get(user_id)
        if caller is None or str(caller.role) not in {
            WorkspaceRole.OWNER.value,
            WorkspaceRole.ADMIN.value,
        }:
            raise WorkspaceAccessDeniedError(ADMIN_REQUIRED)
        if target is None:
            raise UserNotFoundError("Active workspace member not found.")

        previous_role = str(target.role)
        if previous_role != body.role:
            authorize_role_change(
                caller_role=str(caller.role),
                target_role=previous_role,
                new_role=body.role,
                active_owner_count=sum(
                    1 for member in locked if str(member.role) == WorkspaceRole.OWNER.value
                ),
            )
            if loses_contribution(previous_role, body.role):
                # Same reason as revocation: queued work from a user who may no longer
                # contribute must not run later and look like it was allowed.
                await terminalize_member_ingestion_documents(
                    db,
                    workspace_id,
                    user_id,
                    error_message=ROLE_CHANGE_INGESTION_ERROR,
                )
            target.role = body.role
            await db.flush()

        user = await db.get(User, user_id)
        if user is None:
            raise UserNotFoundError("Active workspace member not found.")
        response = WorkspaceMemberResponse(
            user=UserSummary.model_validate(user),
            role=WorkspaceRole(str(target.role)),
            joined_at=target.created_at,
        )
        await db.commit()
    except DBAPIError as exc:
        await db.rollback()
        if _pg_sqlstate(exc) in _RETRYABLE_SQLSTATES or "deadlock detected" in str(exc):
            logger.bind(workspace_id=str(workspace_id), actor_id=str(caller_id)).warning(
                "workspace.member_role_retryable_conflict", sqlstate=_pg_sqlstate(exc)
            )
            raise ServiceUnavailableError(
                "The membership changed concurrently. Please retry.",
                details={"reason": "concurrent_update_retry"},
            ) from exc
        if "last active workspace owner" in str(exc):
            raise WorkspaceAccessDeniedError(LAST_FOUNDER) from exc
        if _is_rls_denial(exc):
            raise WorkspaceAccessDeniedError(ADMIN_CEILING) from exc
        raise

    if previous_role != body.role:
        logger.info(
            "workspace.member_role_changed",
            workspace_id=str(workspace_id),
            target_user_id=str(user_id),
            actor_id=str(caller_id),
            from_role=previous_role,
            to_role=body.role,
        )
    return APIResponse(data=response, meta=_make_meta(request_id, start))
