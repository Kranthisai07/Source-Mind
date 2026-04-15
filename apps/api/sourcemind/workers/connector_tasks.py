"""Celery tasks for GitHub connector sync."""

from __future__ import annotations

import asyncio
import uuid

import structlog
from celery import shared_task

from sourcemind.core.config import get_settings
from sourcemind.core.database import get_db_engine
from sourcemind.core.redis_client import get_redis
from sourcemind.connectors.github.app_auth import GitHubAppAuth
from sourcemind.connectors.github.connector import GitHubConnector
from sourcemind.models.connector import ConnectorConfig

log = structlog.get_logger(__name__)


@shared_task(
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
        result = asyncio.get_event_loop().run_until_complete(
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
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy import select

    settings = get_settings()
    engine = get_db_engine()

    async with AsyncSession(engine) as session:
        result = await session.execute(
            select(ConnectorConfig).where(ConnectorConfig.id == connector_id)
        )
        config = result.scalar_one_or_none()
        if config is None:
            raise ValueError(f"ConnectorConfig not found: {connector_id}")

        redis = await get_redis()
        auth = GitHubAppAuth(
            app_id=settings.github_app_id,
            private_key_pem=settings.github_app_private_key,
            installation_id=settings.github_app_installation_id,
            redis_client=redis,
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
