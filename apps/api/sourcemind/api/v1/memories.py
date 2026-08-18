"""
Memory resource endpoints.

POST   /v1/memories              → ingest content (async, 202 Accepted)
GET    /v1/memories/jobs/:id     → poll ingestion job status
GET    /v1/memories/:id          → retrieve with attribution
PATCH  /v1/memories/:id          → update (triggers attribution recompute)
DELETE /v1/memories/:id          → soft delete
GET    /v1/memories/:id/versions → full version history
"""

import time
from datetime import datetime, timezone
from uuid import UUID

import structlog
from fastapi import APIRouter, Query, status
from sqlalchemy import select, text

from sourcemind.core.dependencies import (
    CurrentUser,
    DBSession,
    IdempotencyKey,
    OpenAIClient,
    RequestID,
)
from sourcemind.core.exceptions import JobNotFoundError, MemoryNotFoundError
from sourcemind.models.document import Document
from sourcemind.models.memory import Memory
from sourcemind.schemas.common import APIResponse, ResponseMeta
from sourcemind.schemas.conflict import MemoryVersionEntry, MemoryVersionsResponse
from sourcemind.schemas.memory import (
    IngestionJobResponse,
    MemoryCreate,
    MemoryResponse,
    MemoryUpdate,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/memories", tags=["memories"])

_DEV_WORKSPACE_ID = UUID("00000000-0000-4000-8000-000000000010")


def _make_meta(request_id: str, start: float) -> ResponseMeta:
    return ResponseMeta(
        request_id=request_id,
        timestamp=datetime.now(timezone.utc),
        latency_ms=(time.perf_counter() - start) * 1000,
    )


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=APIResponse[IngestionJobResponse],
    summary="Ingest content (async)",
    description=(
        "Submit content for asynchronous ingestion. Returns a job_id immediately. "
        "Poll GET /v1/memories/jobs/:id for completion status. "
        "Requires Idempotency-Key header (UUID v4)."
    ),
)
async def create_memory(
    body: MemoryCreate,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
    idempotency_key: IdempotencyKey,
    workspace_id: UUID = Query(
        default=_DEV_WORKSPACE_ID,
        description="Workspace to ingest into. In production, resolved from JWT.",
    ),
) -> APIResponse[IngestionJobResponse]:
    """Stage 1 (RECEIVE) of the ingestion pipeline."""
    start = time.perf_counter()

    effective_ws_id = current_user.workspace_id or workspace_id

    from sourcemind.services.ingestion.receiver import receive

    result = await receive(
        session=db,
        workspace_id=effective_ws_id,
        user_id=current_user.user_id,
        content=body.content,
        url=body.url,
        source_type=body.source_type.value,
        title=body.title,
        # Previously dropped here: MemoryCreate accepted tags and nothing
        # forwarded them, so they never reached the stored memory.
        tags=body.tags,
        idempotency_key=idempotency_key,
    )

    job_resp = IngestionJobResponse(
        job_id=result["job_id"],
        document_id=UUID(result["document_id"]),
        status=result["status"],
        message=result["message"],
        memory_count=result.get("memory_count"),
    )

    return APIResponse(data=job_resp, meta=_make_meta(request_id, start))


@router.get(
    "/jobs/{job_id}",
    response_model=APIResponse[IngestionJobResponse],
    summary="Poll ingestion job status",
)
async def get_ingestion_job(
    job_id: str,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
) -> APIResponse[IngestionJobResponse]:
    """Check the status of an async ingestion job."""
    start = time.perf_counter()

    result = await db.execute(
        select(Document).where(Document.ingestion_job_id == job_id)
    )
    doc = result.scalar_one_or_none()

    if not doc:
        raise JobNotFoundError(f"No job found with id: {job_id}")

    mem_result = await db.execute(
        select(Memory.id).where(
            Memory.document_id == doc.id,
            Memory.current_version.is_(True),
            Memory.deleted_at.is_(None),
        )
    )
    memory_ids = [row[0] for row in mem_result.fetchall()]

    current_stage = (doc.pipeline_data or {}).get("current_stage", doc.ingestion_status)

    job_resp = IngestionJobResponse(
        job_id=job_id,
        document_id=doc.id,
        status=current_stage,
        message=_status_message(doc.ingestion_status, current_stage),
        memory_ids=memory_ids,
        memory_count=doc.memory_count,
        chunk_count=doc.chunk_count,
        error=doc.error_message,
    )

    return APIResponse(data=job_resp, meta=_make_meta(request_id, start))


@router.get(
    "/{memory_id}",
    response_model=APIResponse[MemoryResponse],
    summary="Retrieve a memory with attribution",
)
async def get_memory(
    memory_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
    include_attribution: bool = Query(
        default=False, description="Include contributor attribution breakdown"
    ),
) -> APIResponse[MemoryResponse]:
    """Retrieve a single memory by ID."""
    start = time.perf_counter()

    result = await db.execute(
        select(Memory).where(
            Memory.id == memory_id,
            Memory.deleted_at.is_(None),
        )
    )
    memory = result.scalar_one_or_none()

    if not memory:
        raise MemoryNotFoundError(f"Memory {memory_id} not found.")

    rel_count_result = await db.execute(
        text(
            "SELECT COUNT(*) FROM memory_relations "
            "WHERE source_memory_id = CAST(:id AS uuid) OR target_memory_id = CAST(:id AS uuid)"
        ),
        {"id": str(memory_id)},
    )
    relation_count = rel_count_result.scalar() or 0

    mem_resp = MemoryResponse(
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
    )

    return APIResponse(data=mem_resp, meta=_make_meta(request_id, start))


@router.patch(
    "/{memory_id}",
    response_model=APIResponse[MemoryResponse],
    summary="Update memory content",
    description=(
        "Update the content of a memory. Creates a new version and triggers "
        "attribution recomputation via the 5-signal engine. "
        "Requires Idempotency-Key header."
    ),
)
async def update_memory(
    memory_id: UUID,
    body: MemoryUpdate,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
    idempotency_key: IdempotencyKey,
    openai_client: OpenAIClient,
) -> APIResponse[MemoryResponse]:
    """Update memory content — creates new version + runs 5-signal attribution recompute."""
    start = time.perf_counter()

    from sourcemind.services.attribution.engine import recompute_attribution
    from sourcemind.services.attribution.versioning import create_new_version
    from sourcemind.services.memory.importance import recompute_importance

    version_result = await create_new_version(
        session=db,
        memory_id=memory_id,
        new_content=body.content,
        new_tags=body.tags,
        openai_client=openai_client,
    )

    new_mem = version_result.new_memory
    old_content_result = await db.execute(
        select(Memory).where(Memory.id == version_result.previous_version_id)
    )
    old_mem = old_content_result.scalar_one_or_none()
    content_before = old_mem.content if old_mem else ""

    await recompute_attribution(
        session=db,
        memory_id=new_mem.id,
        editor_id=current_user.user_id,
        content_before=content_before,
        content_after=body.content,
        action_type="edit",
        idempotency_key=idempotency_key,
    )

    await recompute_importance(session=db, memory_id=new_mem.id)

    # importance_score feeds conflict severity, so rescore any conflict
    # touching this memory or severity silently goes stale behind it.
    from sourcemind.services.conflict.severity import recompute_severity_for_memory

    await recompute_severity_for_memory(db, new_mem.id)

    await db.commit()

    mem_resp = MemoryResponse(
        id=new_mem.id,
        workspace_id=new_mem.workspace_id,
        document_id=new_mem.document_id,
        content=new_mem.content,
        version=new_mem.version,
        tags=new_mem.tags,
        category=new_mem.category,
        confidence_score=new_mem.confidence_score,
        created_at=new_mem.created_at or datetime.now(timezone.utc),
        updated_at=new_mem.updated_at,
        relation_count=0,
    )
    return APIResponse(data=mem_resp, meta=_make_meta(request_id, start))


@router.delete(
    "/{memory_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft delete a memory",
)
async def delete_memory(
    memory_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
    idempotency_key: IdempotencyKey,
) -> None:
    """Soft-delete a memory (sets deleted_at)."""
    result = await db.execute(
        select(Memory).where(
            Memory.id == memory_id,
            Memory.deleted_at.is_(None),
        )
    )
    memory = result.scalar_one_or_none()

    if not memory:
        raise MemoryNotFoundError(f"Memory {memory_id} not found.")

    await db.execute(
        text("UPDATE memories SET deleted_at = NOW() WHERE id = CAST(:id AS uuid)"),
        {"id": str(memory_id)},
    )
    await db.commit()


@router.get(
    "/{memory_id}/versions",
    response_model=MemoryVersionsResponse,
    summary="Get full version history",
)
async def get_memory_versions(
    memory_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
) -> MemoryVersionsResponse:
    """Return the complete version chain via recursive CTE."""
    from sourcemind.services.attribution.versioning import get_version_chain

    chain = await get_version_chain(db, memory_id)
    return MemoryVersionsResponse(
        versions=[MemoryVersionEntry(**v) for v in chain],
        total=len(chain),
    )


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _status_message(ingestion_status: str, current_stage: str) -> str:
    messages = {
        "queued": "Document is queued and will be processed shortly.",
        "extracting": "Extracting content from source.",
        "chunking": "Splitting content into chunks.",
        "extracting_facts": "Extracting atomic facts using Claude.",
        "embedding": "Generating embeddings.",
        "indexing": "Writing memories to database.",
        "completed": "Ingestion complete.",
        "failed": "Ingestion failed. Check the 'error' field for details.",
    }
    return messages.get(current_stage, f"Status: {ingestion_status}")
