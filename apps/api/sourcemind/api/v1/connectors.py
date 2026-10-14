"""
Connector management endpoints.

GET    /v1/workspaces/:id/connectors/github/authorize — GitHub App authorization URL
POST   /v1/connectors/github/callback                 — exchange code, return repo picker + ticket
POST   /v1/workspaces/:id/connectors                  — create a connector (needs a ticket)
GET    /v1/workspaces/:id/connectors                  — list connectors
GET    /v1/connectors/:id                             — get connector detail
PATCH  /v1/connectors/:id                             — rename / narrow repos / pause / resume
DELETE /v1/connectors/:id                             — soft disconnect (?remove_memories=)
POST   /v1/connectors/:id/sync                        — trigger a manual sync
POST   /v1/connectors/:id/retry-failed                — re-arm retryable failed documents
POST   /v1/connectors/:id/rescan                      — re-read the import window (does not detect deletions)
GET    /v1/connectors/:id/status                      — state, scopes, counters
GET    /v1/connectors/:id/sync-logs                   — paginated sync history
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from http import HTTPStatus

import structlog
from fastapi import APIRouter, Query, status
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from sourcemind.connectors.github import connect_flow
from sourcemind.core.config import get_settings
from sourcemind.core.dependencies import (
    CurrentUser,
    DBSession,
    RequestID,
    WorkspacePermission,
    require_connector_access,
    require_workspace_permission,
)
from sourcemind.core.exceptions import (
    ConnectorNotFoundError,
    ErrorCode,
    ForbiddenError,
    ServiceUnavailableError,
    SourceMindError,
)
from sourcemind.core.rate_limit import RateLimitedOperation, enforce_rate_limit
from sourcemind.models.connector import ConnectorConfig, ConnectorSyncLog
from sourcemind.schemas.connector import (
    ConnectorCreateRequest,
    ConnectorListResponse,
    ConnectorResponse,
    ConnectorStatusResponse,
    ConnectorUpdateRequest,
    CurrentRunProgress,
    DisconnectResponse,
    DocumentCounters,
    GitHubAuthorizeResponse,
    GitHubCallbackRequest,
    GitHubCallbackResponse,
    GitHubRepoOption,
    RescanResponse,
    RetryFailedResponse,
    ScopeStatusItem,
    SyncLogResponse,
    SyncLogsResponse,
    SyncTriggerRequest,
    SyncTriggerResponse,
    parse_github_config,
)
from sourcemind.services.connectors import connect_ticket, lifecycle
from sourcemind.services.ingestion import receiver
from sourcemind.services.memory.retire import retire_document_memories

log = structlog.get_logger(__name__)

router = APIRouter(tags=["connectors"])

_RECENT_LOGS = 10


def _conflict(code: str, message: str) -> SourceMindError:
    return SourceMindError(
        message,
        details={"reason": code},
        code=ErrorCode.VALIDATION_ERROR,
        http_status=HTTPStatus.CONFLICT,
    )


async def _load_connector(db: DBSession, connector_id: uuid.UUID) -> ConnectorConfig:
    connector = (
        await db.execute(select(ConnectorConfig).where(ConnectorConfig.id == connector_id))
    ).scalar_one_or_none()
    if connector is None:
        # Unreachable in practice - the gate before it 404s first - but kept so the
        # ORM load has a defined outcome (SM027 like every other connector 404).
        raise ConnectorNotFoundError(f"Connector {connector_id} not found.")
    return connector


# ── Connect flow ──────────────────────────────────────────────────────────────

@router.get(
    "/workspaces/{workspace_id}/connectors/github/authorize",
    response_model=GitHubAuthorizeResponse,
    summary="Start the GitHub App authorization",
)
async def github_authorize(
    workspace_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
) -> GitHubAuthorizeResponse:
    """Return the GitHub App authorization URL carrying a signed single-use ``state``."""
    await require_workspace_permission(
        db, current_user.user_id, workspace_id, WorkspacePermission.ADMINISTER
    )
    await connect_ticket.enforce_connector_rate_limit(
        "connector-authorize", current_user.user_id, workspace_id
    )
    settings = get_settings()
    if not settings.github_app_client_id or not settings.github_app_client_secret:
        raise ServiceUnavailableError("GitHub connection is not configured.")
    state = await connect_ticket.issue_state(
        client_secret=settings.github_app_client_secret,
        workspace_id=workspace_id,
        user_id=current_user.user_id,
    )
    return GitHubAuthorizeResponse(
        authorize_url=connect_flow.build_authorize_url(settings.github_app_client_id, state),
        state=state,
        expires_in_seconds=connect_ticket.STATE_TTL_SECONDS,
    )


@router.post(
    "/connectors/github/callback",
    response_model=GitHubCallbackResponse,
    summary="Complete the GitHub App authorization",
)
async def github_callback(
    body: GitHubCallbackRequest,
    db: DBSession,
    current_user: CurrentUser,
) -> GitHubCallbackResponse:
    """Validate ``state``, exchange ``code`` once, and return the repository picker.

    Only the user-and-installation repository list is kept (in a Redis ticket); the user
    access token is discarded inside ``connect_flow`` and never persisted or logged.
    """
    settings = get_settings()
    await connect_ticket.enforce_connector_rate_limit(
        "connector-callback", current_user.user_id, None
    )
    if not settings.github_app_client_secret:
        raise ServiceUnavailableError("GitHub connection is not configured.")
    try:
        workspace_id = await connect_ticket.consume_state(
            client_secret=settings.github_app_client_secret,
            state=body.state,
            user_id=current_user.user_id,
        )
    except connect_ticket.ConnectTicketError as exc:
        raise ForbiddenError(
            "The GitHub authorization expired or is invalid. Start again.",
            details={"reason": exc.code},
        ) from None
    await require_workspace_permission(
        db, current_user.user_id, workspace_id, WorkspacePermission.ADMINISTER
    )
    try:
        auth = await connect_flow.authorize_user(
            body.code,
            client_id=settings.github_app_client_id,
            client_secret=settings.github_app_client_secret,
            installation_id=body.installation_id,
        )
    except connect_flow.ConnectFlowError as exc:
        unavailable = exc.code in {"github_unavailable", "not_configured", "github_bad_response"}
        raise SourceMindError(
            "GitHub authorization could not be completed.",
            details={"reason": exc.code},
            code=ErrorCode.SERVICE_UNAVAILABLE if unavailable else ErrorCode.VALIDATION_ERROR,
            http_status=(
                HTTPStatus.SERVICE_UNAVAILABLE if unavailable else HTTPStatus.UNPROCESSABLE_ENTITY
            ),
        ) from None
    ticket_id = await connect_ticket.store_ticket(
        workspace_id=workspace_id,
        user_id=current_user.user_id,
        github_user_id=auth.github_user_id,
        installation_id=auth.installation_id,
        repositories=[repo.full_name for repo in auth.repositories],
    )
    return GitHubCallbackResponse(
        ticket_id=ticket_id,
        expires_in_seconds=connect_ticket.TICKET_TTL_SECONDS,
        workspace_id=workspace_id,
        installation_id=auth.installation_id,
        github_user_id=auth.github_user_id,
        github_login=auth.github_login,
        repositories=[
            GitHubRepoOption(
                full_name=repo.full_name,
                private=repo.private,
                default_branch=repo.default_branch,
            )
            for repo in auth.repositories
        ],
        truncated=auth.truncated,
    )


# ── Create connector ──────────────────────────────────────────────────────────

@router.post(
    "/workspaces/{workspace_id}/connectors",
    response_model=ConnectorResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create connector",
)
async def create_connector(
    workspace_id: uuid.UUID,
    body: ConnectorCreateRequest,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
) -> ConnectorResponse:
    """Register a GitHub connector for repositories the caller's ticket covers."""
    await require_workspace_permission(
        db, current_user.user_id, workspace_id, WorkspacePermission.ADMINISTER
    )
    await connect_ticket.enforce_connector_rate_limit(
        "connector-create", current_user.user_id, workspace_id
    )
    config = parse_github_config(body.config)
    try:
        ticket = await connect_ticket.load_ticket(
            body.ticket_id or "", workspace_id=workspace_id, user_id=current_user.user_id
        )
    except connect_ticket.ConnectTicketError as exc:
        raise ForbiddenError(
            "The GitHub authorization expired or is invalid. Connect again.",
            details={"reason": exc.code},
        ) from None
    canonical = [ticket.canonical_repo(repo) for repo in config.repos]
    if any(repo is None for repo in canonical):
        raise ForbiddenError(
            "One or more repositories are not covered by your GitHub authorization.",
            details={"reason": "repository_not_authorized"},
        )

    try:
        connector = await lifecycle.create_connector(
            db,
            workspace_id=workspace_id,
            created_by=current_user.user_id,
            display_name=body.display_name,
            config=config,
            installation_id=ticket.installation_id,
            authorized_github_user_id=ticket.github_user_id,
            repos=[repo for repo in canonical if repo is not None],
        )
    except IntegrityError:
        await db.rollback()
        raise _conflict(
            "connector_exists", "A connector with this name already exists."
        ) from None
    # Burn the ticket only once the row is accepted; of two concurrent creates, one loses.
    if not await connect_ticket.claim_ticket(ticket.ticket_id):
        await db.rollback()
        raise ForbiddenError(
            "The GitHub authorization expired or is invalid. Connect again.",
            details={"reason": "connect_ticket_invalid"},
        )
    await db.commit()

    log.info(
        "connector_created",
        connector_id=str(connector.id),
        connector_type="github",
        workspace_id=str(workspace_id),
        repos=len(config.repos),
        request_id=request_id,
    )
    return ConnectorResponse.model_validate(connector)


# ── List connectors ───────────────────────────────────────────────────────────

@router.get(
    "/workspaces/{workspace_id}/connectors",
    response_model=ConnectorListResponse,
    summary="List connectors",
)
async def list_connectors(
    workspace_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
    connector_type: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
) -> ConnectorListResponse:
    """List all connectors for the given workspace, with optional filters."""
    await require_workspace_permission(
        db, current_user.user_id, workspace_id, WorkspacePermission.ADMINISTER
    )

    query = select(ConnectorConfig).where(ConnectorConfig.workspace_id == workspace_id)
    if connector_type:
        query = query.where(ConnectorConfig.connector_type == connector_type)
    if status_filter:
        query = query.where(ConnectorConfig.status == status_filter)
    query = query.order_by(ConnectorConfig.created_at.desc())

    connectors = (await db.execute(query)).scalars().all()
    return ConnectorListResponse(
        items=[ConnectorResponse.model_validate(c) for c in connectors],
        total=len(connectors),
    )


# ── Get connector ─────────────────────────────────────────────────────────────

@router.get(
    "/connectors/{connector_id}",
    response_model=ConnectorResponse,
    summary="Get connector",
)
async def get_connector(
    connector_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
) -> ConnectorResponse:
    """Retrieve a single connector configuration by ID."""
    await require_connector_access(db, current_user.user_id, connector_id)
    return ConnectorResponse.model_validate(await _load_connector(db, connector_id))


# ── Update connector ──────────────────────────────────────────────────────────

@router.patch(
    "/connectors/{connector_id}",
    response_model=ConnectorResponse,
    summary="Update connector",
)
async def update_connector(
    connector_id: uuid.UUID,
    body: ConnectorUpdateRequest,
    db: DBSession,
    current_user: CurrentUser,
) -> ConnectorResponse:
    """Rename, narrow the repository list, pause or resume."""
    await require_connector_access(db, current_user.user_id, connector_id)
    connector = await _load_connector(db, connector_id)
    if connector.status == "disconnected":
        raise _conflict("connector_disconnected", "The connector is disconnected.")

    if body.display_name is not None:
        connector.display_name = body.display_name
    if body.config is not None:
        new = parse_github_config(body.config)
        existing = {repo.casefold() for repo in connector.config.get("repos", [])}
        if any(repo.casefold() not in existing for repo in new.repos):
            raise ForbiddenError(
                "Repositories can only be removed here; connect again to add one.",
                details={"reason": "repository_not_authorized"},
            )
        connector.config = new.model_dump(exclude_none=True)
    await db.flush()
    try:
        if body.status == "paused" and connector.status != "paused":
            await lifecycle.pause(db, connector_id)
        elif body.status == "active" and connector.status != "active":
            await lifecycle.resume(db, connector_id, now=datetime.now(UTC))
    except lifecycle.LifecycleConflictError as exc:
        raise _conflict(exc.code, "The connector is disconnected.") from None
    await db.flush()
    await db.refresh(connector)
    await db.commit()
    return ConnectorResponse.model_validate(connector)


# ── Disconnect ────────────────────────────────────────────────────────────────

@router.delete(
    "/connectors/{connector_id}",
    response_model=DisconnectResponse,
    summary="Disconnect connector",
)
async def delete_connector(
    connector_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
    remove_memories: bool = Query(default=False),
) -> DisconnectResponse:
    """Soft disconnect: stop all work, keep rows, logs and links.

    ``remove_memories=true`` also retires imported machine-only memories; lineages with a
    human edit are flagged stale and stay searchable.
    """
    await require_connector_access(db, current_user.user_id, connector_id)
    await _load_connector(db, connector_id)
    result = await lifecycle.disconnect(
        db,
        connector_id,
        remove_memories=remove_memories,
        retire=retire_document_memories,
    )
    await db.commit()
    return DisconnectResponse(
        connector_id=connector_id,
        status="disconnected",
        generation=result.generation,
        remove_memories=remove_memories,
        documents_considered=result.documents_considered,
        memories_retired=result.memories_retired,
        memories_flagged=result.memories_flagged,
    )


# ── Trigger sync ──────────────────────────────────────────────────────────────

@router.post(
    "/connectors/{connector_id}/sync",
    response_model=SyncTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger connector sync",
)
async def trigger_sync(
    connector_id: uuid.UUID,
    body: SyncTriggerRequest,
    db: DBSession,
    current_user: CurrentUser,
) -> SyncTriggerResponse:
    """Enqueue a manual sync (refused for disconnected, paused or unbound connectors)."""
    from sourcemind.workers.connector_tasks import sync_github_connector

    workspace_id = await require_connector_access(db, current_user.user_id, connector_id)
    await enforce_rate_limit(
        RateLimitedOperation.CONNECTOR_SYNC, current_user.user_id, workspace_id
    )
    connector = await _load_connector(db, connector_id)
    if connector.status in ("disconnected", "paused"):
        raise _conflict(f"connector_{connector.status}", f"The connector is {connector.status}.")
    if connector.installation_id is None or connector.authorized_github_user_id is None:
        raise _conflict("authorization_missing", "The connector must be connected again.")

    task = sync_github_connector.apply_async(
        kwargs={
            "connector_id": str(connector_id),
            "workspace_id": str(connector.workspace_id),
            "user_id": str(current_user.user_id),
            "sync_type": body.sync_type,
        }
    )
    log.info(
        "connector_sync_triggered",
        connector_id=str(connector_id),
        sync_type=body.sync_type,
        task_id=task.id,
    )
    return SyncTriggerResponse(
        task_id=task.id,
        connector_id=connector_id,
        sync_type=body.sync_type,
        message=f"Sync task enqueued (type={body.sync_type})",
    )


# ── Retry / re-scan ───────────────────────────────────────────────────────────

@router.post(
    "/connectors/{connector_id}/retry-failed",
    response_model=RetryFailedResponse,
    summary="Retry failed documents",
)
async def retry_failed(
    connector_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
) -> RetryFailedResponse:
    """Re-arm retryable failed documents of the current generation."""
    workspace_id = await require_connector_access(db, current_user.user_id, connector_id)
    await connect_ticket.enforce_connector_rate_limit(
        "connector-retry", current_user.user_id, workspace_id
    )
    requeue = getattr(receiver, "requeue_transient_failure", None)
    if requeue is None:
        raise ServiceUnavailableError("Retry is not available yet.")
    try:
        result = await lifecycle.retry_failed(db, connector_id, requeue=requeue)
    except lifecycle.LifecycleConflictError as exc:
        raise _conflict(exc.code, "The connector is disconnected.") from None
    await db.commit()
    return RetryFailedResponse(
        connector_id=connector_id,
        eligible=result.eligible,
        requeued=result.requeued,
        refused=result.refused,
        remaining=result.remaining,
    )


@router.post(
    "/connectors/{connector_id}/rescan",
    response_model=RescanResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-scan connector scopes",
)
async def rescan_connector(
    connector_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
) -> RescanResponse:
    """Restart every scope from the start of the import window; the next sync re-reads it.

    Deleted sources are NOT detected (automatic deletion detection is off in this release), so
    a re-scan never retires memories.
    """
    workspace_id = await require_connector_access(db, current_user.user_id, connector_id)
    await connect_ticket.enforce_connector_rate_limit(
        "connector-rescan", current_user.user_id, workspace_id
    )
    connector = await _load_connector(db, connector_id)
    try:
        marked = await lifecycle.rescan(
            db, connector_id, config=connector.config, now=datetime.now(UTC)
        )
    except lifecycle.LifecycleConflictError as exc:
        raise _conflict(exc.code, f"The connector is {connector.status}.") from None
    await db.commit()
    return RescanResponse(
        connector_id=connector_id,
        scopes_marked=marked,
        message=(
            "Scopes restarted from the start of the import window; the next sync re-reads it. "
            "Sources deleted on GitHub are not detected automatically."
        ),
    )


# ── Status ────────────────────────────────────────────────────────────────────

def _derive_state(connector: ConnectorConfig, running: bool, scopes_ok: bool) -> str:
    if connector.status in ("disconnected", "paused", "paused_budget", "error"):
        return connector.status
    if running:
        return "syncing"
    return "idle" if scopes_ok else "needs_attention"


@router.get(
    "/connectors/{connector_id}/status",
    response_model=ConnectorStatusResponse,
    summary="Connector status",
)
async def connector_status(
    connector_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
) -> ConnectorStatusResponse:
    """State, scope table, document counters, filtered counts and recent sync logs."""
    await require_connector_access(db, current_user.user_id, connector_id)
    connector = await _load_connector(db, connector_id)

    logs = (
        await db.execute(
            select(ConnectorSyncLog)
            .where(ConnectorSyncLog.connector_id == connector_id)
            .order_by(ConnectorSyncLog.started_at.desc())
            .limit(_RECENT_LOGS)
        )
    ).scalars().all()
    running_log = (
        await db.execute(
            select(ConnectorSyncLog)
            .where(
                ConnectorSyncLog.connector_id == connector_id,
                ConnectorSyncLog.status == "running",
            )
            .order_by(ConnectorSyncLog.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    scope_rows = (
        await db.execute(
            text(
                "SELECT repo, artifact_type, status, phase, listing_complete, "
                "last_error_code, last_progress_at, authorization_verified_at, "
                "window_low, window_high FROM connector_sync_cursors "
                "WHERE connector_id = :cid ORDER BY repo, artifact_type"
            ),
            {"cid": connector_id},
        )
    ).mappings().all()
    scopes = [ScopeStatusItem(**dict(row)) for row in scope_rows]

    counters = DocumentCounters()
    doc_rows = (
        await db.execute(
            text(
                "SELECT ingestion_status AS s, count(*) AS n, "
                "count(*) FILTER (WHERE ingestion_status = 'completed' AND ("
                "  metadata ->> 'publication' = 'no_facts' "
                "  OR COALESCE(memory_count, 0) = 0)) AS zero "
                "FROM documents WHERE connector_id = :cid AND deleted_at IS NULL "
                "GROUP BY ingestion_status"
            ),
            {"cid": connector_id},
        )
    ).mappings().all()
    for row in doc_rows:
        if row["s"] in ("pending", "processing", "completed", "failed"):
            setattr(counters, row["s"], int(row["n"]))
        counters.zero_facts += int(row["zero"])
    counters.quarantined = int(
        (
            await db.execute(
                text(
                    "SELECT count(*) FROM connector_work_items "
                    "WHERE connector_id = :cid AND state = 'quarantined'"
                ),
                {"cid": connector_id},
            )
        ).scalar_one()
    )

    filtered: dict[str, int] = {}
    for (mapping,) in (
        await db.execute(
            select(ConnectorSyncLog.artifacts_filtered).where(
                ConnectorSyncLog.connector_id == connector_id
            )
        )
    ).all():
        for reason, count in (mapping or {}).items():
            if isinstance(count, int):
                filtered[reason] = filtered.get(reason, 0) + count

    current_run: CurrentRunProgress | None = None
    if running_log is not None:
        current_run = CurrentRunProgress(
            sync_log_id=running_log.id,
            trigger=running_log.trigger,  # type: ignore[arg-type]
            sync_type=running_log.sync_type,  # type: ignore[arg-type]
            started_at=running_log.started_at,
            artifacts_found=running_log.artifacts_found,
            artifacts_new=running_log.artifacts_new,
            artifacts_updated=running_log.artifacts_updated,
            artifacts_skipped=running_log.artifacts_skipped,
            artifacts_failed=running_log.artifacts_failed,
            artifacts_filtered=running_log.artifacts_filtered or {},
        )
    state = _derive_state(
        connector, running_log is not None, all(s.status == "ok" for s in scopes)
    )
    return ConnectorStatusResponse(
        connector_id=connector.id,
        workspace_id=connector.workspace_id,
        connector_type=connector.connector_type,  # type: ignore[arg-type]
        display_name=connector.display_name,
        status=connector.status,  # type: ignore[arg-type]
        state=state,  # type: ignore[arg-type]
        generation=connector.generation,
        last_sync_at=connector.last_sync_at,
        next_sync_at=connector.next_sync_at,
        last_success_at=connector.last_success_at,
        last_error_code=connector.last_error_code,
        current_run=current_run,
        scopes=scopes,
        documents=counters,
        filtered=filtered,
        recent_logs=[SyncLogResponse.model_validate(entry) for entry in logs],
    )


# ── Sync logs ─────────────────────────────────────────────────────────────────

@router.get(
    "/connectors/{connector_id}/sync-logs",
    response_model=SyncLogsResponse,
    summary="List sync logs",
)
async def list_sync_logs(
    connector_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0, ge=0),
) -> SyncLogsResponse:
    """Return paginated sync history for a connector."""
    await require_connector_access(db, current_user.user_id, connector_id)

    total = (
        await db.execute(
            select(func.count()).where(ConnectorSyncLog.connector_id == connector_id)
        )
    ).scalar_one()
    logs = (
        await db.execute(
            select(ConnectorSyncLog)
            .where(ConnectorSyncLog.connector_id == connector_id)
            .order_by(ConnectorSyncLog.started_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return SyncLogsResponse(
        items=[SyncLogResponse.model_validate(entry) for entry in logs],
        total=total,
    )
