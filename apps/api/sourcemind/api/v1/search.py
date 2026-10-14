"""
Search endpoint — hybrid semantic + keyword search over workspace memories.

POST /v1/memories/search

Search modes:
  hybrid  (default) — Reciprocal Rank Fusion combining pgvector + BM25
  semantic          — pgvector cosine similarity only
  keyword           — PostgreSQL tsvector BM25 ranking only

Latency targets:
  p50 < 200ms, p95 < 500ms (enforced in performance tests)

Access control:
  Results are always filtered to the requesting user's workspace.
  Viewers receive truncated content; members/admins receive full content.
"""

import time
from uuid import UUID

import structlog
from fastapi import APIRouter, Query

from sourcemind.core.config import get_settings
from sourcemind.core.dependencies import (
    CurrentUser,
    DBSession,
    OpenAIClient,
    RequestID,
    WorkspacePermission,
    require_workspace_permission,
)
from sourcemind.core.exceptions import ValidationError
from sourcemind.core.rate_limit import RateLimitedOperation, enforce_rate_limit
from sourcemind.schemas.memory import (
    SearchRequest,
    SearchResponse,
    SearchResultItem,
)
from sourcemind.services.memory.read import (
    SourceContext,
    fetch_source_context,
    memory_response_from_search_item,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/memories", tags=["search"])

@router.post(
    "/search",
    response_model=SearchResponse,
    response_model_exclude_none=True,
    summary="Hybrid semantic + keyword search",
    description=(
        "Search workspace memories using Reciprocal Rank Fusion of vector "
        "similarity and BM25 keyword ranking. "
        # Deliberately does NOT say "and role". Role never filters which
        # memories are returned - it only truncates content for viewers (see
        # hybrid_search step 4). Claiming role-based filtering in the public
        # API description overstates the access control that actually runs.
        "Results are filtered to the requesting user's workspace; viewers "
        "receive truncated content."
    ),
)
async def search_memories(
    body: SearchRequest,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
    openai_client: OpenAIClient,
    workspace_id: UUID = Query(
        ...,
        description="Workspace to search.",
    ),
) -> SearchResponse:
    """Execute a hybrid search over workspace memories."""
    from sourcemind.services.search.hybrid import hybrid_search

    t0 = time.perf_counter()
    if body.filters is not None:
        raise ValidationError(
            "Search filters are not implemented; refusing to ignore supplied filters."
        )

    user_role = await require_workspace_permission(
        db,
        current_user.user_id,
        workspace_id,
        WorkspacePermission.READ,
    )
    await enforce_rate_limit(
        RateLimitedOperation.SEARCH, current_user.user_id, workspace_id
    )

    search_result = await hybrid_search(
        session=db,
        query=body.query,
        workspace_id=workspace_id,
        limit=body.limit,
        min_similarity=body.min_similarity,
        mode=body.mode.value,
        user_role=user_role,
        include_attribution=body.include_attribution,
        openai_client=openai_client,
    )

    ctx_by_id: dict[str, SourceContext] = {}
    if body.include_source and search_result["results"]:
        ctx_by_id = await fetch_source_context(
            db, [item["id"] for item in search_result["results"]]
        )

    results: list[SearchResultItem] = []
    for rank, item in enumerate(search_result["results"], start=1):
        results.append(
            SearchResultItem(
                memory=memory_response_from_search_item(
                    item, workspace_id, ctx_by_id.get(item["id"])
                ),
                score=item["score"],
                rank=rank,
                match_type=item["match_type"],
            )
        )

    latency_ms = (time.perf_counter() - t0) * 1000
    search_event_id = None
    if get_settings().pilot_search_evidence_enabled:
        from sourcemind.services.pilot_evidence import record_search_event

        event = await record_search_event(
            db,
            workspace_id=workspace_id,
            requester_user_id=current_user.user_id,
            request_id=request_id,
            request=body,
            results=results,
        )
        search_event_id = event.id

    return SearchResponse(
        results=results,
        total_found=search_result["total_found"],
        query=body.query,
        mode=body.mode,
        latency_ms=latency_ms,
        search_event_id=search_event_id,
    )
