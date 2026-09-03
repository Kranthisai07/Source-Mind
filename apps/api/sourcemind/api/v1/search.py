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
from datetime import UTC, datetime
from uuid import UUID

import structlog
from fastapi import APIRouter, Query

from sourcemind.core.dependencies import CurrentUser, DBSession, OpenAIClient, RequestID
from sourcemind.schemas.memory import (
    MemoryResponse,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/memories", tags=["search"])

_DEV_WORKSPACE_ID = UUID("00000000-0000-4000-8000-000000000010")


@router.post(
    "/search",
    response_model=SearchResponse,
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
        default=_DEV_WORKSPACE_ID,
        description="Workspace to search. In production, resolved from JWT.",
    ),
) -> SearchResponse:
    """Execute a hybrid search over workspace memories."""
    from sourcemind.services.search.hybrid import hybrid_search

    t0 = time.perf_counter()

    effective_ws_id = current_user.workspace_id or workspace_id
    user_role = current_user.workspace_role or "member"

    search_result = await hybrid_search(
        session=db,
        query=body.query,
        workspace_id=effective_ws_id,
        limit=body.limit,
        min_similarity=body.min_similarity,
        mode=body.mode.value,
        user_role=user_role,
        include_attribution=body.include_attribution,
        openai_client=openai_client,
    )

    results: list[SearchResultItem] = []
    for rank, item in enumerate(search_result["results"], start=1):
        mem_resp = MemoryResponse(
            id=UUID(item["id"]),
            workspace_id=effective_ws_id,
            document_id=None,
            content=item["content"],
            version=1,
            tags=None,
            category=None,
            confidence_score=None,
            created_at=datetime.now(UTC),
            updated_at=None,
            # hybrid_search attaches this when include_attribution is set, and
            # MemoryResponse has always had the field, but the route never
            # passed it through. The flag was accepted and its extra query
            # paid for, then the result dropped on the floor here.
            attribution=item.get("attribution"),
            relation_count=0,
        )
        results.append(
            SearchResultItem(
                memory=mem_resp,
                score=item["score"],
                rank=rank,
                match_type=item["match_type"],
            )
        )

    latency_ms = (time.perf_counter() - t0) * 1000

    return SearchResponse(
        results=results,
        total_found=search_result["total_found"],
        query=body.query,
        mode=body.mode,
        latency_ms=latency_ms,
    )
