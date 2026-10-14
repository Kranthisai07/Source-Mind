"""Shared read path for memories: provenance, authorship, stale state, search.

One implementation serves the REST routes and the MCP interface so neither can
drift: both expose the same ``source``, ``authorship`` and ``source_state``
objects built here, and both authorize through the same guards.

Authorization is never delegated to a caller-supplied workspace id. Every
public entry point takes the authenticated ``user_id`` and:

* sets the trusted RLS user context on the session (the database enforces
  active workspace membership independently of this module),
* asserts workspace membership and READ permission (``require_workspace_permission``)
  or memory access (``require_memory_access``), and
* maps "not a member" and "does not exist" to the same not-found error so the
  response is not an existence oracle.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.core.database import set_rls_user_context
from sourcemind.core.dependencies import (
    WorkspacePermission,
    require_memory_access,
    require_workspace_permission,
)
from sourcemind.core.exceptions import MemoryNotFoundError
from sourcemind.core.rate_limit import RateLimitedOperation, enforce_rate_limit
from sourcemind.models.memory import Memory
from sourcemind.schemas.memory import (
    Authorship,
    MemoryResponse,
    MemorySource,
    SearchResultItem,
    SourceState,
)
from sourcemind.services.attribution.versioning import find_latest_version

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class SourceContext:
    """Provenance of one memory, or ``None`` fields when it has no source."""

    source: MemorySource | None
    authorship: Authorship | None
    source_state: SourceState | None


_EMPTY = SourceContext(None, None, None)


def authorship_from_link(
    *,
    source_author: str | None,
    resolved: bool,
    link_metadata: dict[str, Any] | None,
) -> Authorship | None:
    """Authorship of the source text. Never invents a split between authors.

    ``shared`` is recorded by the connector (``metadata.authorship == 'shared'``)
    for artifacts with several authors, e.g. a commit with co-author trailers.
    """
    meta = link_metadata or {}
    if meta.get("authorship") == "shared":
        authors = [str(a) for a in (meta.get("source_authors") or []) if a]
        return Authorship(status="shared", source_authors=authors or None)
    if resolved:
        return Authorship(status="resolved", source_author=source_author)
    if source_author:
        return Authorship(status="unresolved", source_author=source_author)
    return None


async def fetch_source_context(
    session: AsyncSession, memory_ids: Sequence[str]
) -> dict[str, SourceContext]:
    """Source, authorship and stale state for the given memories, one query.

    Derived from the DOCUMENT-level artifact link (every version of a memory
    shares its document), choosing the newest revision recorded for that
    document. ``newer_source_url`` is the URL of the newest revision of the same
    artifact, so a stale memory can point at what replaced it.
    """
    ids = [str(i) for i in memory_ids]
    if not ids:
        return {}
    rows = (
        await session.execute(
            text(
                """
                SELECT
                    m.id::text AS memory_id,
                    m.source_stale_at,
                    m.source_stale_reason,
                    link.source_tool,
                    link.source_type,
                    link.source_id,
                    link.source_url,
                    link.source_author,
                    link.source_timestamp,
                    link.source_revision,
                    link.revision_seq,
                    link.resolved_user_id IS NOT NULL AS resolved,
                    link.link_metadata,
                    latest.source_url AS latest_url
                FROM memories m
                LEFT JOIN LATERAL (
                    SELECT al.source_tool, al.source_type, al.source_id,
                           al.source_url, al.source_author, al.source_timestamp,
                           al.source_revision, al.revision_seq,
                           al.resolved_user_id, al.workspace_id,
                           al."metadata" AS link_metadata
                    FROM artifact_links al
                    WHERE al.document_id = m.document_id
                    ORDER BY al.revision_seq DESC, al.created_at, al.id
                    LIMIT 1
                ) link ON TRUE
                LEFT JOIN LATERAL (
                    SELECT al2.source_url
                    FROM artifact_links al2
                    WHERE link.source_id IS NOT NULL
                      AND al2.workspace_id = link.workspace_id
                      AND al2.source_tool = link.source_tool
                      AND al2.source_type = link.source_type
                      AND al2.source_id = link.source_id
                    ORDER BY al2.revision_seq DESC, al2.created_at DESC
                    LIMIT 1
                ) latest ON TRUE
                WHERE m.id = ANY((:ids)::uuid[])
                """
            ),
            {"ids": ids},
        )
    ).all()

    out: dict[str, SourceContext] = {}
    for row in rows:
        if row.source_id is None:
            out[row.memory_id] = _EMPTY
            continue
        source = MemorySource(
            tool=row.source_tool,
            type=row.source_type,
            id=row.source_id,
            url=row.source_url,
            revision=row.source_revision,
            revision_seq=row.revision_seq,
            observed_at=row.source_timestamp,
        )
        authorship = authorship_from_link(
            source_author=row.source_author,
            resolved=bool(row.resolved),
            link_metadata=row.link_metadata,
        )
        if row.source_stale_at is not None:
            state = SourceState(
                status="stale",
                since=row.source_stale_at,
                reason=row.source_stale_reason,
                newer_source_url=row.latest_url if row.latest_url != row.source_url else None,
            )
        else:
            state = SourceState(status="current")
        out[row.memory_id] = SourceContext(source, authorship, state)
    return out


def memory_response_from_search_item(
    item: dict[str, Any],
    workspace_id: UUID,
    ctx: SourceContext | None = None,
) -> MemoryResponse:
    """Build the API memory object from a ``hybrid_search`` result row."""
    ctx = ctx or _EMPTY
    return MemoryResponse(
        id=UUID(item["id"]),
        workspace_id=workspace_id,
        document_id=UUID(item["document_id"]) if item["document_id"] else None,
        content=item["content"],
        version=item["version"],
        tags=item["tags"],
        category=item["category"],
        confidence_score=item["confidence_score"],
        created_at=item["created_at"],
        updated_at=item["updated_at"],
        # hybrid_search attaches these when include_attribution is set.
        attribution=item.get("attribution"),
        unresolved_author=item.get("unresolved_author"),
        relation_count=item["relation_count"],
        source=ctx.source,
        authorship=ctx.authorship,
        source_state=ctx.source_state,
    )


async def build_memory_response(
    session: AsyncSession,
    memory: Memory,
    *,
    user_id: UUID,
    include_attribution: bool,
    include_source: bool = True,
) -> MemoryResponse:
    """Full memory representation for an ALREADY AUTHORIZED, loaded memory."""
    memory_id = memory.id
    relation_count = (
        await session.execute(
            text(
                "SELECT COUNT(*) FROM memory_relations "
                "WHERE source_memory_id = CAST(:id AS uuid) "
                "OR target_memory_id = CAST(:id AS uuid)"
            ),
            {"id": str(memory_id)},
        )
    ).scalar() or 0

    attribution = None
    unresolved_author = None
    if include_attribution:
        # Reuse hybrid.py's helpers: one implementation of the DISTINCT ON that
        # keeps the append-only attributions table from inflating percentages.
        from sourcemind.services.search.hybrid import (
            _fetch_attributions,
            _fetch_unresolved_authors,
        )

        by_memory = await _fetch_attributions(session, [str(memory_id)])
        attribution = by_memory.get(str(memory_id)) or []
        unresolved_author = (
            await _fetch_unresolved_authors(session, [str(memory_id)])
        ).get(str(memory_id))

    latest = await find_latest_version(
        session, memory_id, memory.workspace_id, user_id=user_id
    )
    ctx = _EMPTY
    if include_source:
        ctx = (await fetch_source_context(session, [str(memory_id)])).get(
            str(memory_id), _EMPTY
        )

    return MemoryResponse(
        id=memory.id,
        workspace_id=memory.workspace_id,
        document_id=memory.document_id,
        content=memory.content,
        version=memory.version,
        tags=memory.tags,
        category=memory.category,
        confidence_score=memory.confidence_score,
        created_at=memory.created_at,
        updated_at=memory.updated_at,
        relation_count=relation_count,
        attribution=attribution,
        unresolved_author=unresolved_author,
        is_current=latest.reason is None,
        latest_memory_id=latest.latest_memory_id,
        supersession_reason=latest.reason.value if latest.reason else None,
        source=ctx.source,
        authorship=ctx.authorship,
        source_state=ctx.source_state,
    )


async def get_memory_for_user(
    session: AsyncSession,
    *,
    user_id: UUID,
    memory_id: UUID,
    include_attribution: bool = True,
) -> MemoryResponse:
    """Authorize and load one memory for an authenticated user (MCP entry).

    A memory in a workspace the user cannot read is indistinguishable from one
    that does not exist: both raise ``MemoryNotFoundError``.
    """
    await set_rls_user_context(session, user_id)
    await require_memory_access(session, user_id, memory_id)
    memory = (
        await session.execute(
            select(Memory).where(Memory.id == memory_id, Memory.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if memory is None:
        raise MemoryNotFoundError(f"Memory {memory_id} not found.")
    return await build_memory_response(
        session,
        memory,
        user_id=user_id,
        include_attribution=include_attribution,
        include_source=True,
    )


@dataclass(frozen=True)
class SearchOutcome:
    results: list[SearchResultItem]
    total_found: int
    user_role: str


async def search_memories_for_user(
    session: AsyncSession,
    *,
    user_id: UUID,
    workspace_id: UUID,
    query: str,
    mode: str = "hybrid",
    limit: int = 10,
    min_similarity: float = 0.3,
    include_attribution: bool = True,
    include_source: bool = True,
    openai_client: object | None = None,
) -> SearchOutcome:
    """Authorize and run a workspace search for an authenticated user (MCP entry).

    ``workspace_id`` is only a selector: membership and READ are asserted here
    on every call, and the database independently enforces RLS.
    """
    from sourcemind.services.search.hybrid import hybrid_search

    await set_rls_user_context(session, user_id)
    role = await require_workspace_permission(
        session, user_id, workspace_id, WorkspacePermission.READ
    )
    await enforce_rate_limit(RateLimitedOperation.SEARCH, user_id, workspace_id)
    raw = await hybrid_search(
        session=session,
        query=query,
        workspace_id=workspace_id,
        limit=limit,
        min_similarity=min_similarity,
        mode=mode,
        user_role=role,
        include_attribution=include_attribution,
        openai_client=openai_client,
    )
    ctx_by_id: dict[str, SourceContext] = {}
    if include_source and raw["results"]:
        ctx_by_id = await fetch_source_context(session, [r["id"] for r in raw["results"]])
    items = [
        SearchResultItem(
            memory=memory_response_from_search_item(
                item, workspace_id, ctx_by_id.get(item["id"])
            ),
            score=item["score"],
            rank=rank,
            match_type=item["match_type"],
        )
        for rank, item in enumerate(raw["results"], start=1)
    ]
    return SearchOutcome(items, raw["total_found"], str(role))
