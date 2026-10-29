"""Graph-backed read endpoints.

GET /v1/memories/:id/lineage                          version chain with contributors per version
GET /v1/memories/:id/related?depth=&types=&limit=     typed neighbourhood
GET /v1/memories/:id/owners                           who would know, with the path that explains it
GET /v1/workspaces/:id/analytics/collaboration-graph  contributors and the memories they share

Each answers from Neo4j when it is enabled, fresh and agrees with PostgreSQL, and from PostgreSQL
otherwise; ``source`` in the body says which (services/graph/reads.py). Authorisation always runs
first, in PostgreSQL, and its workspace id is the only thing that scopes a graph query.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query

from sourcemind.core.dependencies import (
    CurrentUser,
    DBSession,
    RequestID,
    require_memory_access,
    require_workspace_member,
)
from sourcemind.core.rate_limit import RateLimitedOperation, enforce_rate_limit
from sourcemind.schemas.common import APIResponse, ResponseMeta
from sourcemind.schemas.graph import (
    CollaborationResponse,
    LineageResponse,
    OwnersResponse,
    RelatedResponse,
    RelationType,
)
from sourcemind.services.graph import reads

router = APIRouter(tags=["graph"])


def _meta(request_id: str, started: float) -> ResponseMeta:
    return ResponseMeta(
        request_id=request_id,
        timestamp=datetime.now(UTC),
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
    )


@router.get(
    "/memories/{memory_id}/lineage",
    response_model=APIResponse[LineageResponse],
    summary="Version chain with the contributors of each version",
)
async def memory_lineage(
    memory_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
) -> APIResponse[LineageResponse]:
    started = time.perf_counter()
    workspace_id = await require_memory_access(db, current_user.user_id, memory_id)
    await enforce_rate_limit(RateLimitedOperation.ANALYTICS, current_user.user_id, workspace_id)
    data = await reads.lineage(db, memory_id, workspace_id)
    return APIResponse(data=data, meta=_meta(request_id, started))


@router.get(
    "/memories/{memory_id}/related",
    response_model=APIResponse[RelatedResponse],
    summary="Memories related to this one, up to three hops",
)
async def memory_related(
    memory_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
    depth: Annotated[int, Query(ge=1, le=reads.MAX_DEPTH, description="Hops to follow")] = 1,
    types: Annotated[list[RelationType] | None, Query(description="Relation types")] = None,
    limit: Annotated[int, Query(ge=1, le=reads.MAX_RELATED_LIMIT)] = reads.DEFAULT_RELATED_LIMIT,
) -> APIResponse[RelatedResponse]:
    started = time.perf_counter()
    workspace_id = await require_memory_access(db, current_user.user_id, memory_id)
    await enforce_rate_limit(RateLimitedOperation.ANALYTICS, current_user.user_id, workspace_id)
    data = await reads.related(
        db, memory_id, workspace_id, depth=depth, types=list(types) if types else None, limit=limit
    )
    return APIResponse(data=data, meta=_meta(request_id, started))


@router.get(
    "/memories/{memory_id}/owners",
    response_model=APIResponse[OwnersResponse],
    summary="Who would know about this memory",
)
async def memory_owners(
    memory_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
) -> APIResponse[OwnersResponse]:
    started = time.perf_counter()
    workspace_id = await require_memory_access(db, current_user.user_id, memory_id)
    await enforce_rate_limit(RateLimitedOperation.ANALYTICS, current_user.user_id, workspace_id)
    data = await reads.owners(db, memory_id, workspace_id)
    return APIResponse(data=data, meta=_meta(request_id, started))


@router.get(
    "/workspaces/{workspace_id}/analytics/collaboration-graph",
    response_model=APIResponse[CollaborationResponse],
    summary="Contributors and the memories they share",
)
async def collaboration_graph(
    workspace_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
) -> APIResponse[CollaborationResponse]:
    started = time.perf_counter()
    await require_workspace_member(db, current_user.user_id, workspace_id)
    await enforce_rate_limit(RateLimitedOperation.ANALYTICS, current_user.user_id, workspace_id)
    data = await reads.collaboration(db, workspace_id)
    return APIResponse(data=data, meta=_meta(request_id, started))
