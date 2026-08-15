"""Celery tasks for GitHub connector sync."""

from __future__ import annotations

import asyncio
import uuid

import structlog

from sourcemind.core.config import get_settings
# Bind to the CONFIGURED app, not celery's current_app. @shared_task resolves
# against whatever app happens to be current, which in the API process is a
# default Celery instance pointing at localhost:6379 — publishing then fails
# with "connection refused" instead of reaching the real broker.
from sourcemind.workers.celery_app import app as celery_app
from sourcemind.core.redis_client import close_redis, get_redis, init_redis
from sourcemind.connectors.github.app_auth import GitHubAppAuth
from sourcemind.connectors.github.connector import GitHubConnector
from sourcemind.models.connector import ConnectorConfig

log = structlog.get_logger(__name__)


@celery_app.task(
    name="sourcemind.workers.connector_tasks.sync_github_connector",
    bind=True,
    max_retries=3,
    default_retry_delay=120,
    acks_late=True,
)
def sync_github_connector(
    self,
    connector_id: str,
    workspace_id: str,
    user_id: str,
    sync_type: str = "incremental",
) -> dict:
    """Celery task: run a GitHub connector sync.

    Args:
        connector_id: UUID string of the ConnectorConfig row.
        workspace_id: UUID string of the owning workspace.
        user_id: UUID string of the system user triggering the sync.
        sync_type: ``"full"`` or ``"incremental"``.

    Returns:
        Dict with sync result summary.
    """
    log.info(
        "connector_sync_task_started",
        connector_id=connector_id,
        sync_type=sync_type,
    )
    try:
        result = asyncio.run(
            _run_sync(
                connector_id=uuid.UUID(connector_id),
                workspace_id=uuid.UUID(workspace_id),
                user_id=uuid.UUID(user_id),
                sync_type=sync_type,
            )
        )
        log.info(
            "connector_sync_task_completed",
            connector_id=connector_id,
            **result,
        )
        return result
    except Exception as exc:
        log.error(
            "connector_sync_task_failed",
            connector_id=connector_id,
            error=str(exc),
        )
        raise self.retry(exc=exc)


async def _run_sync(
    connector_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    sync_type: str,
) -> dict:
    """Async implementation of the connector sync."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    settings = get_settings()

    # A Celery worker process never runs the FastAPI lifespan, so the module
    # globals behind get_engine()/get_redis() are unset. Build a dedicated
    # engine and initialise Redis for this task, mirroring workers/ingestion.py.
    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=0,
    )
    await init_redis()

    try:
        async with AsyncSession(engine) as session:
            result = await session.execute(
                select(ConnectorConfig).where(ConnectorConfig.id == connector_id)
            )
            config = result.scalar_one_or_none()
            if config is None:
                raise ValueError(f"ConnectorConfig not found: {connector_id}")

            auth = GitHubAppAuth(
                app_id=settings.github_app_id,
                private_key_pem=settings.github_app_private_key,
                installation_id=settings.github_app_installation_id,
                redis_client=get_redis(),
            )

            connector = GitHubConnector(
                config=config,
                auth=auth,
                session=session,
                workspace_id=workspace_id,
                user_id=user_id,
            )

            sync_log = await connector.sync(sync_type=sync_type)
            await session.commit()

            return {
                "status": sync_log.status,
                "artifacts_found": sync_log.artifacts_found,
                "artifacts_new": sync_log.artifacts_new,
                "artifacts_skipped": sync_log.artifacts_skipped,
                "error_message": sync_log.error_message,
            }
    finally:
        await engine.dispose()
        await close_redis()
