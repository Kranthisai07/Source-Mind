"""Celery tasks for GitHub connector sync: the scheduler tick and the sync run."""

from __future__ import annotations

import asyncio
import random
import uuid
from types import SimpleNamespace
from typing import Any

import structlog

from sourcemind.connectors.github.app_auth import GitHubAppAuth
from sourcemind.connectors.github.connector import GitHubConnector
from sourcemind.connectors.github.contracts import TransientError
from sourcemind.core.config import get_settings
from sourcemind.core.database import set_rls_user_context, set_rls_workspace_context
from sourcemind.core.redis_client import close_redis, get_redis, init_redis

# Bind to the CONFIGURED app, not celery's current_app. @shared_task resolves
# against whatever app happens to be current, which in the API process is a
# default Celery instance pointing at localhost:6379 — publishing then fails
# with "connection refused" instead of reaching the real broker.
from sourcemind.workers.celery_app import app as celery_app

log = structlog.get_logger(__name__)

_TICK_BATCH = 100
_MAX_RETRIES = 4


def _backoff_seconds(retries: int) -> int:
    """Exponential backoff with jitter for retried runs."""
    base = min(900, 30 * (2**retries))
    return int(base + random.uniform(0, base / 2))  # noqa: S311 - jitter, not security


# ── scheduler tick ────────────────────────────────────────────────────────────


@celery_app.task(
    name="sourcemind.workers.connector_tasks.dispatch_due_syncs",
    acks_late=True,
)
def dispatch_due_syncs() -> dict:
    """Beat tick: enqueue ``sync_connector`` for every due connector (no user context)."""
    due = asyncio.run(_list_due(_TICK_BATCH))
    dispatched = 0
    for row in due:
        # The run uses the creator's RLS context. When the creator is no longer an active
        # owner/admin the connector is invisible to them, so an active admin's context is
        # used instead: the orchestrator then records ``owner_inactive`` and stops.
        acting = row.get("created_by") if row.get("creator_active") else row.get("acting_admin")
        if not acting:
            log.warning("connector_tick_no_admin", connector_id=str(row["connector_id"]))
            continue
        sync_connector.apply_async(
            kwargs={
                "connector_id": str(row["connector_id"]),
                "workspace_id": str(row["workspace_id"]),
                "user_id": str(acting),
                "trigger": "scheduled",
            }
        )
        dispatched += 1
    log.info("connector_tick_dispatched", due=len(due), dispatched=dispatched)
    return {"dispatched": dispatched}


async def _list_due(limit: int) -> list[dict[str, Any]]:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(
        get_settings().async_database_url, pool_pre_ping=True, pool_size=1, max_overflow=0
    )
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(
                text("SELECT * FROM sm_list_due_connectors(:n)"), {"n": limit}
            )
            return [dict(r) for r in rows.mappings()]
    finally:
        await engine.dispose()


# ── sync run ──────────────────────────────────────────────────────────────────


def _run_task(
    task: Any,
    connector_id: str,
    workspace_id: str,
    user_id: str | None,
    trigger: str,
    sync_type: str,
) -> dict:
    log.info("connector_sync_task_started", connector_id=connector_id, trigger=trigger)
    try:
        result = asyncio.run(
            _run_sync(
                connector_id=uuid.UUID(connector_id),
                workspace_id=uuid.UUID(workspace_id),
                user_id=uuid.UUID(user_id) if user_id else None,
                trigger=trigger,
                sync_type=sync_type,
            )
        )
    except TransientError:
        log.warning("connector_sync_task_transient", connector_id=connector_id)
        raise task.retry(countdown=_backoff_seconds(task.request.retries)) from None
    except Exception as exc:
        log.error(
            "connector_sync_task_failed",
            connector_id=connector_id,
            error_type=type(exc).__name__,
        )
        raise task.retry(
            exc=RuntimeError("Connector sync failed."),
            countdown=_backoff_seconds(task.request.retries),
        ) from None
    log.info("connector_sync_task_completed", connector_id=connector_id, **result)
    return result


@celery_app.task(
    name="sourcemind.workers.connector_tasks.sync_connector",
    bind=True,
    max_retries=_MAX_RETRIES,
    acks_late=True,
)
def sync_connector(
    self,
    connector_id: str,
    workspace_id: str,
    user_id: str | None = None,
    trigger: str = "scheduled",
    sync_type: str = "incremental",
) -> dict:
    """Run one sync as the connector creator (scheduled) or the initiator (manual).

    ``TransientError`` is retried with exponential backoff and jitter; every other
    outcome is reported in the return value (the sync log carries the detail).
    """
    return _run_task(self, connector_id, workspace_id, user_id, trigger, sync_type)


@celery_app.task(
    name="sourcemind.workers.connector_tasks.sync_github_connector",
    bind=True,
    max_retries=_MAX_RETRIES,
    acks_late=True,
)
def sync_github_connector(
    self,
    connector_id: str,
    workspace_id: str,
    user_id: str,
    sync_type: str = "incremental",
) -> dict:
    """Manual trigger (name kept for the API): the same run with ``trigger='manual'``."""
    return _run_task(self, connector_id, workspace_id, user_id, "manual", sync_type)


async def _run_sync(
    connector_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID | None,
    trigger: str,
    sync_type: str,
) -> dict:
    """Async implementation: open the RLS-scoped session and run the orchestrator."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from sourcemind.models.connector import ConnectorConfig

    if user_id is None:
        # A run needs a user context; the tick always supplies one, so this is a caller bug.
        log.warning("connector_sync_creator_unknown", connector_id=str(connector_id))
        return {"status": "rejected", "error_code": "creator_unknown"}

    settings = get_settings()
    # A Celery worker process never runs the FastAPI lifespan: build a dedicated
    # engine and initialise Redis for this task, mirroring workers/ingestion.py.
    engine = create_async_engine(
        settings.async_database_url, pool_pre_ping=True, pool_size=2, max_overflow=0
    )
    await init_redis()
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            await set_rls_user_context(session, user_id)
            await set_rls_workspace_context(session, workspace_id)
            config = (
                await session.execute(
                    select(ConnectorConfig).where(
                        ConnectorConfig.id == connector_id,
                        ConnectorConfig.workspace_id == workspace_id,
                    )
                )
            ).scalar_one_or_none()
            # rollback() expires every ORM instance and an expired attribute cannot be
            # lazily refreshed in async code (MissingGreenlet), so copy what the run needs
            # first and hand the connector a plain snapshot, never the live row.
            snapshot = (
                None
                if config is None
                else SimpleNamespace(id=config.id, installation_id=config.installation_id)
            )
            await session.rollback()
            if snapshot is None:
                log.warning("connector_sync_not_visible", connector_id=str(connector_id))
                return {"status": "rejected", "error_code": "connector_not_visible"}

            auth = GitHubAppAuth(
                app_id=settings.github_app_id,
                private_key_pem=settings.github_app_private_key,
                installation_id=snapshot.installation_id or settings.github_app_installation_id,
                redis_client=get_redis(),
            )
            connector = GitHubConnector(
                config=snapshot,
                auth=auth,
                session=session,
                workspace_id=workspace_id,
                user_id=user_id,
                verifier=_build_verifier(auth),
            )
            outcome = await connector.run(trigger=trigger, sync_type=sync_type)
            return {
                "status": outcome.status,
                "error_code": outcome.error_code,
                "artifacts_found": outcome.counts.get("found", 0),
                "artifacts_new": outcome.counts.get("new", 0),
                "artifacts_skipped": outcome.counts.get("skipped", 0),
            }
    finally:
        await engine.dispose()
        await close_redis()


def _build_verifier(auth: GitHubAppAuth) -> Any:
    """The repository-access verifier (contract C11), wired to the installation token."""
    from sourcemind.connectors.github.verifier import build_verifier

    return build_verifier(auth)
