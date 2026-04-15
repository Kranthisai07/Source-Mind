"""GitHubConnector — orchestrates syncing a GitHub repo into SourceMind memories.

Sync flow per repository:
  1. Fetch artifact batches (commits, PRs, issues, discussions) via GitHubClient.
  2. Map each raw payload to a ConnectorDocument via GitHubMapper.
  3. Check ArtifactLink for duplicates (dedup by source_tool + source_type + source_id).
  4. For new artifacts, call the ingestion receiver to create a Document + enqueue pipeline.
  5. Insert ArtifactLink rows linking memory → source artifact.
  6. Write a ConnectorSyncLog record.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.connectors.github.app_auth import GitHubAppAuth
from sourcemind.connectors.github.client import GitHubClient
from sourcemind.connectors.github.mapper import ConnectorDocument, GitHubMapper
from sourcemind.models.connector import ArtifactLink, ConnectorConfig, ConnectorSyncLog
from sourcemind.models.document import DocumentSourceType
from sourcemind.services.ingestion.receiver import receive

log = structlog.get_logger(__name__)


class GitHubConnector:
    """Orchestrates a full or incremental sync for a single GitHub connector config.

    Args:
        config: The :class:`ConnectorConfig` ORM row for this connector.
        auth: Configured :class:`GitHubAppAuth` for token management.
        session: Active async database session.
        workspace_id: Workspace UUID that owns this connector.
        user_id: Service/system user UUID used as the ingestion actor.
    """

    def __init__(
        self,
        config: ConnectorConfig,
        auth: GitHubAppAuth,
        session: AsyncSession,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        self._config = config
        self._client = GitHubClient(auth)
        self._session = session
        self._workspace_id = workspace_id
        self._user_id = user_id

    # ── Public API ────────────────────────────────────────────────────────────

    async def sync(self, sync_type: str = "incremental") -> ConnectorSyncLog:
        """Run a full or incremental sync for this connector.

        Args:
            sync_type: ``"full"`` ignores ``last_sync_at``; ``"incremental"``
                       uses it as a ``since`` filter.

        Returns:
            The :class:`ConnectorSyncLog` row (already flushed, not committed).
        """
        since: str | None = None
        if sync_type == "incremental" and self._config.last_sync_at:
            since = self._config.last_sync_at.isoformat()

        sync_log = ConnectorSyncLog(
            connector_id=self._config.id,
            sync_type=sync_type,
            status="running",
            artifacts_found=0,
            artifacts_new=0,
            artifacts_skipped=0,
            started_at=datetime.now(timezone.utc),
        )
        self._session.add(sync_log)
        await self._session.flush()

        repos: list[str] = self._config.config.get("repos", [])
        total_found = total_new = total_skipped = 0

        try:
            for repo_full in repos:
                parts = repo_full.split("/", 1)
                if len(parts) != 2:
                    log.warning("github_connector_invalid_repo", repo=repo_full)
                    continue
                owner, repo = parts
                found, new, skipped = await self._sync_repo(owner, repo, since)
                total_found += found
                total_new += new
                total_skipped += skipped

            sync_log.status = "completed"
        except Exception as exc:
            sync_log.status = "failed"
            sync_log.error_message = str(exc)
            log.error(
                "github_connector_sync_failed",
                connector_id=str(self._config.id),
                error=str(exc),
            )
        finally:
            sync_log.artifacts_found = total_found
            sync_log.artifacts_new = total_new
            sync_log.artifacts_skipped = total_skipped
            sync_log.completed_at = datetime.now(timezone.utc)

            # Update connector timestamps
            self._config.last_sync_at = datetime.now(timezone.utc)
            await self._session.flush()

        return sync_log

    # ── Per-repo sync ─────────────────────────────────────────────────────────

    async def _sync_repo(
        self, owner: str, repo: str, since: str | None
    ) -> tuple[int, int, int]:
        """Sync all artifact types for a single repository.

        Returns:
            Tuple of (found, new, skipped) artifact counts.
        """
        found = new = skipped = 0
        mapper = GitHubMapper()

        # ── Commits ───────────────────────────────────────────────────────────
        async for raw_commit in self._client.get_commits(owner, repo, since=since):
            doc = GitHubMapper.from_commit(owner, repo, raw_commit)
            found += 1
            ingested = await self._ingest(doc)
            if ingested:
                new += 1
            else:
                skipped += 1

        # ── Pull Requests ─────────────────────────────────────────────────────
        async for raw_pr in self._client.get_pull_requests(owner, repo, since=since):
            doc = GitHubMapper.from_pull_request(owner, repo, raw_pr)
            found += 1
            ingested = await self._ingest(doc)
            if ingested:
                new += 1
            else:
                skipped += 1

        # ── Issues ────────────────────────────────────────────────────────────
        async for raw_issue in self._client.get_issues(owner, repo, since=since):
            doc = GitHubMapper.from_issue(owner, repo, raw_issue)
            found += 1
            ingested = await self._ingest(doc)
            if ingested:
                new += 1
            else:
                skipped += 1

        # ── Discussions ───────────────────────────────────────────────────────
        try:
            discussions = await self._client.get_discussions(owner, repo)
            for raw_discussion in discussions:
                doc = GitHubMapper.from_discussion(owner, repo, raw_discussion)
                found += 1
                ingested = await self._ingest(doc)
                if ingested:
                    new += 1
                else:
                    skipped += 1
        except Exception as exc:
            # Discussions may be disabled for the repo — non-fatal
            log.warning(
                "github_discussions_unavailable",
                owner=owner, repo=repo, error=str(exc),
            )

        log.info(
            "github_repo_sync_complete",
            owner=owner,
            repo=repo,
            found=found,
            new=new,
            skipped=skipped,
        )
        return found, new, skipped

    # ── Dedup + ingest ────────────────────────────────────────────────────────

    async def _ingest(self, doc: ConnectorDocument) -> bool:
        """Ingest one artifact, skipping if already ingested (via ArtifactLink).

        Returns:
            ``True`` if the artifact was newly ingested, ``False`` if skipped.
        """
        # Dedup check — ArtifactLink unique constraint covers workspace+tool+type+id
        existing = await self._session.execute(
            select(ArtifactLink).where(
                ArtifactLink.workspace_id == self._workspace_id,
                ArtifactLink.source_tool == doc.source_tool,
                ArtifactLink.source_type == doc.source_type,
                ArtifactLink.source_id == doc.source_id,
            )
        )
        if existing.scalar_one_or_none() is not None:
            return False

        # Dispatch via ingestion receiver
        try:
            result = await receive(
                session=self._session,
                workspace_id=self._workspace_id,
                user_id=self._user_id,
                content=doc.content,
                source_type=DocumentSourceType.TEXT,
                title=doc.title,
                idempotency_key=doc.idempotency_key,
            )
        except Exception as exc:
            log.warning(
                "github_connector_ingest_failed",
                source_id=doc.source_id,
                error=str(exc),
            )
            return False

        if result.get("already_exists"):
            return False

        # Record the artifact link for future dedup
        document_id = result.get("document_id")
        if document_id:
            link = ArtifactLink(
                workspace_id=self._workspace_id,
                memory_id=uuid.UUID(str(document_id)),
                source_tool=doc.source_tool,
                source_type=doc.source_type,
                source_id=doc.source_id,
                source_url=doc.source_url,
                source_author=doc.source_author,
                source_timestamp=doc.source_timestamp,
                artifact_metadata=doc.metadata,
            )
            self._session.add(link)
            await self._session.flush()

        return True
