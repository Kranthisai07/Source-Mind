"""
Memory resource endpoints.

POST   /v1/memories              → ingest content (async, 202 Accepted)
GET    /v1/memories/jobs/:id     → poll ingestion job status
GET    /v1/memories/:id          → retrieve with attribution
PATCH  /v1/memories/:id          → update (triggers attribution recompute)
POST   /v1/memories/:id/reapply  → re-apply an edit to a replacement of a source-retired memory
DELETE /v1/memories/:id          → soft delete
GET    /v1/memories/:id/versions → full version history
"""

import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID

import structlog
from fastapi import APIRouter, Query, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.core.dependencies import (
    CurrentUser,
    DBSession,
    IdempotencyKey,
    OpenAIClient,
    RequestID,
    WorkspacePermission,
    require_memory_access,
    require_workspace_permission,
)
from sourcemind.core.exceptions import (
    JobNotFoundError,
    MemoryNotFoundError,
    MemoryVersionStaleError,
    NoReplacementError,
    ValidationError,
    WorkspaceNotFoundError,
)
from sourcemind.core.rate_limit import RateLimitedOperation, enforce_rate_limit
from sourcemind.models.document import Document
from sourcemind.models.memory import Memory
from sourcemind.schemas.common import APIResponse, ResponseMeta
from sourcemind.schemas.conflict import MemoryVersionEntry, MemoryVersionsResponse
from sourcemind.schemas.memory import (
    IngestionJobResponse,
    MemoryCreate,
    MemoryReapply,
    MemoryResponse,
    MemoryUpdate,
)
from sourcemind.services.memory.read import build_memory_response

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/memories", tags=["memories"])

def _make_meta(request_id: str, start: float) -> ResponseMeta:
    return ResponseMeta(
        request_id=request_id,
        timestamp=datetime.now(UTC),
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
        ...,
        description="Workspace to ingest into.",
    ),
) -> APIResponse[IngestionJobResponse]:
    """Stage 1 (RECEIVE) of the ingestion pipeline."""
    start = time.perf_counter()

    await require_workspace_permission(
        db,
        current_user.user_id,
        workspace_id,
        WorkspacePermission.CONTRIBUTE,
    )
    await enforce_rate_limit(
        RateLimitedOperation.INGEST, current_user.user_id, workspace_id
    )

    from sourcemind.services.ingestion.receiver import receive

    result = await receive(
        session=db,
        workspace_id=workspace_id,
        user_id=current_user.user_id,
        content=body.content,
        url=body.url,
        source_type=body.source_type.value,
        title=body.title,
        # Previously dropped here: MemoryCreate accepted tags and nothing
        # forwarded them, so they never reached the stored memory.
        tags=body.tags,
        category=body.category,
        ingestion_mode=body.ingestion_mode,
        idempotency_key=idempotency_key,
    )

    job_resp = IngestionJobResponse(
        job_id=result["job_id"],
        document_id=UUID(result["document_id"]),
        status=result["status"],
        message=result["message"],
        memory_count=result.get("memory_count"),
        # The receiver already knew this; it used to be dropped here. Status and
        # message semantics are unchanged - the flag only lets a client avoid
        # claiming that an identical earlier submission created new memories.
        already_exists=bool(result.get("already_exists", False)),
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

    # The job is addressed by an opaque id, but it is workspace-scoped through
    # its document. A non-member gets the same JobNotFoundError as a bad id.
    try:
        await require_workspace_permission(
            db,
            current_user.user_id,
            doc.workspace_id,
            WorkspacePermission.READ,
        )
    except WorkspaceNotFoundError:
        raise JobNotFoundError(f"No job found with id: {job_id}") from None

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

    await require_memory_access(db, current_user.user_id, memory_id)

    result = await db.execute(
        select(Memory).where(
            Memory.id == memory_id,
            Memory.deleted_at.is_(None),
        )
    )
    memory = result.scalar_one_or_none()

    if not memory:
        raise MemoryNotFoundError(f"Memory {memory_id} not found.")

    mem_resp = await build_memory_response(
        db,
        memory,
        user_id=current_user.user_id,
        include_attribution=include_attribution,
        include_source=True,
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

    await require_memory_access(
        db, current_user.user_id, memory_id, WorkspacePermission.CONTRIBUTE
    )
    mem_resp = await _apply_edit(
        db,
        current_user.user_id,
        memory_id,
        content=body.content,
        tags=body.tags,
        idempotency_key=idempotency_key,
        openai_client=openai_client,
    )
    return APIResponse(data=mem_resp, meta=_make_meta(request_id, start))


async def _apply_edit(
    db: AsyncSession,
    user_id: UUID,
    memory_id: UUID,
    *,
    content: str,
    tags: list[str] | None,
    idempotency_key: str,
    openai_client: object | None,
    base_version: int | None = None,
    revalidate: Callable[[Memory], Awaitable[None]] | None = None,
) -> MemoryResponse:
    """The one edit path shared by PATCH and re-apply: lock, revalidate, version, attribute.

    CONTRIBUTE access to ``memory_id`` must already have passed. ``revalidate`` runs
    after the head row lock and before any write (re-apply uses it to re-derive the
    candidate set under the target's lock).
    """
    from sourcemind.services.attribution.engine import (
        assert_edit_preserves_inherited_contributors,
        carry_forward_attribution,
        plan_carry_forward,
        recompute_attribution,
    )
    from sourcemind.services.attribution.versioning import (
        create_new_version,
        lock_editable_head,
    )
    from sourcemind.services.memory.importance import recompute_importance

    # Exact string equality defines "unchanged"; no normalisation. The row is
    # locked and revalidated so the checks below and the version insert see one
    # stable state. A memory retired by a source refresh answers 409
    # source_retired (with candidate replacements) instead of a bare 404; a user
    # delete, a missing row and a superseded version keep their existing answers
    # (404 / 409 SM035). No version is created and the edit is never applied to
    # the latest version; idempotent replay success is not implemented (D-025).
    current = await lock_editable_head(db, memory_id, user_id=user_id)
    if revalidate is not None:
        await revalidate(current)
    if base_version is not None and base_version != current.version:
        raise MemoryVersionStaleError(
            f"Memory {memory_id} is at version {current.version}, not {base_version}.",
            details={
                "memory_id": str(memory_id),
                "latest_memory_id": str(memory_id),
                "reason": "base_version_mismatch",
                "current_version": current.version,
            },
        )
    content_changed = current.content != content
    tags_changed = tags is not None and list(tags) != list(current.tags or [])

    if not content_changed and not tags_changed:
        # Nothing to record: no new version and no contribution event.
        return MemoryResponse(
            id=current.id,
            workspace_id=current.workspace_id,
            document_id=current.document_id,
            content=current.content,
            version=current.version,
            tags=current.tags,
            category=current.category,
            confidence_score=current.confidence_score,
            created_at=current.created_at or datetime.now(UTC),
            updated_at=current.updated_at,
            relation_count=0,
        )

    # Read-only refusals, evaluated BEFORE any write so a refusal leaves no new
    # version, no current_version change and no attribution rows (D-022).
    if content_changed:
        await assert_edit_preserves_inherited_contributors(db, memory_id)
    else:
        await plan_carry_forward(db, memory_id)

    content_before = current.content
    version_result = await create_new_version(
        session=db,
        memory_id=memory_id,
        new_content=content,
        new_tags=tags,
        openai_client=openai_client,
        user_id=user_id,
    )

    new_mem = version_result.new_memory

    if content_changed:
        await recompute_attribution(
            session=db,
            memory_id=new_mem.id,
            editor_id=user_id,
            content_before=content_before,
            content_after=content,
            action_type="edit",
            idempotency_key=idempotency_key,
        )
    else:
        # Tags-only: new version, same content. Expose the prior attribution
        # on it without recording an authorship event.
        await carry_forward_attribution(
            db,
            from_memory_id=version_result.previous_version_id,
            to_memory_id=new_mem.id,
        )

    await recompute_importance(session=db, memory_id=new_mem.id)

    # importance_score feeds conflict severity, so rescore any conflict
    # touching this memory or severity silently goes stale behind it.
    from sourcemind.services.conflict.severity import recompute_severity_for_memory

    await recompute_severity_for_memory(db, new_mem.id)

    await db.commit()

    return MemoryResponse(
        id=new_mem.id,
        workspace_id=new_mem.workspace_id,
        document_id=new_mem.document_id,
        content=new_mem.content,
        version=new_mem.version,
        tags=new_mem.tags,
        category=new_mem.category,
        confidence_score=new_mem.confidence_score,
        created_at=new_mem.created_at or datetime.now(UTC),
        updated_at=new_mem.updated_at,
        relation_count=0,
    )


@router.post(
    "/{memory_id}/reapply",
    response_model=APIResponse[MemoryResponse],
    summary="Re-apply an edit to a replacement of a source-retired memory",
    description=(
        "A PATCH answered 409 source_retired with candidate replacements. Re-apply the "
        "draft to ONE explicitly chosen candidate (target_memory_id). The candidates are "
        "re-derived server side; the server never chooses. The edit is recorded under the "
        "acting user on the target, which keeps its own contribution history. "
        "Requires Idempotency-Key header."
    ),
)
async def reapply_memory_edit(
    memory_id: UUID,
    body: MemoryReapply,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
    idempotency_key: IdempotencyKey,
    openai_client: OpenAIClient,
) -> APIResponse[MemoryResponse]:
    """Re-apply a draft to an explicit replacement candidate of a source-retired memory."""
    from sourcemind.services.attribution.versioning import find_source_retired_candidates
    from sourcemind.services.memory.retire import SOURCE_RETIRE_REASONS

    start = time.perf_counter()
    await require_memory_access(
        db, current_user.user_id, memory_id, WorkspacePermission.CONTRIBUTE
    )

    retired = (
        await db.execute(select(Memory).where(Memory.id == memory_id))
    ).scalar_one_or_none()
    if retired is None:
        raise MemoryNotFoundError(f"Memory {memory_id} not found.")
    if retired.deleted_at is None or retired.deleted_reason not in SOURCE_RETIRE_REASONS:
        raise ValidationError(
            "Only a memory retired by a source refresh can be re-applied.",
            details={"memory_id": str(memory_id), "reason": "not_source_retired"},
        )

    async def _candidate_ids() -> set[UUID]:
        _, found = await find_source_retired_candidates(
            db, workspace_id=retired.workspace_id, document_id=retired.document_id
        )
        return {mid for mid, _ in found}

    candidates = await _candidate_ids()
    if not candidates:
        raise NoReplacementError(
            "The source has no replacement memory; nothing was created.",
            details={"memory_id": str(memory_id)},
        )

    def _not_a_candidate() -> ValidationError:
        return ValidationError(
            "target_memory_id is not a current replacement candidate.",
            details={"reason": "target_not_a_candidate"},
        )

    if body.target_memory_id not in candidates:
        raise _not_a_candidate()
    # Candidates are workspace-scoped by construction; the target still needs the
    # caller's own CONTRIBUTE access.
    await require_memory_access(
        db, current_user.user_id, body.target_memory_id, WorkspacePermission.CONTRIBUTE
    )

    async def _target_still_candidate(_locked: Memory) -> None:
        # Re-derived under the target's row lock so a publication that committed
        # in between cannot leave a stale candidate list in force.
        if body.target_memory_id not in await _candidate_ids():
            raise _not_a_candidate()

    mem_resp = await _apply_edit(
        db,
        current_user.user_id,
        body.target_memory_id,
        content=body.content,
        tags=body.tags,
        idempotency_key=idempotency_key,
        openai_client=openai_client,
        base_version=body.base_version,
        revalidate=_target_still_candidate,
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
    await require_memory_access(
        db, current_user.user_id, memory_id, WorkspacePermission.ADMINISTER
    )

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
        text(
            "UPDATE memories SET deleted_at = NOW(), deleted_reason = 'user_deleted' "
            "WHERE id = CAST(:id AS uuid)"
        ),
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
    await require_memory_access(db, current_user.user_id, memory_id)

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
        "retrying": "A temporary ingestion error occurred. Retrying shortly.",
        "completed": "Ingestion complete.",
        "failed": "Ingestion failed. Check the 'error' field for details.",
    }
    return messages.get(current_stage, f"Status: {ingestion_status}")
