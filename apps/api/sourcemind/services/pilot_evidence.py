"""Persistence and validation for append-only pilot search evidence."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.core.exceptions import IdempotencyConflictError, ValidationError
from sourcemind.models.search_evidence import SearchEvent, SearchRating
from sourcemind.schemas.memory import SearchRequest, SearchResultItem
from sourcemind.schemas.pilot import RatingSource, SearchRatingCreate

SEARCH_ALGORITHM_ID = "memory-search-v1"


async def record_search_event(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    requester_user_id: uuid.UUID,
    request_id: str,
    request: SearchRequest,
    results: list[SearchResultItem],
) -> SearchEvent:
    snapshot = [result.model_dump(mode="json") for result in results]
    event = SearchEvent(
        workspace_id=workspace_id,
        requester_user_id=requester_user_id,
        query=request.query,
        search_parameters=request.model_dump(mode="json"),
        request_id=request_id,
        result_snapshot=snapshot,
        memory_ids=[str(result.memory.id) for result in results],
        document_ids=[
            str(result.memory.document_id) if result.memory.document_id is not None else None
            for result in results
        ],
        algorithm_id=f"{SEARCH_ALGORITHM_ID}:{request.mode.value}",
    )
    session.add(event)
    await session.flush()
    return event


def _result_rank(event: SearchEvent, memory_id: uuid.UUID) -> int | None:
    expected = str(memory_id)
    for position, result in enumerate(event.result_snapshot, start=1):
        memory = result.get("memory") if isinstance(result, dict) else None
        if isinstance(memory, dict) and memory.get("id") == expected:
            return position
    return None


async def _validate_named_rater(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    memory_id: uuid.UUID,
    rater_user_id: uuid.UUID,
    source: RatingSource,
) -> None:
    active_member = (
        await session.execute(
            text(
                "SELECT 1 FROM workspace_members "
                "WHERE workspace_id = CAST(:workspace_id AS uuid) "
                "AND user_id = CAST(:user_id AS uuid) "
                "AND status = 'active' AND departed_at IS NULL"
            ),
            {"workspace_id": str(workspace_id), "user_id": str(rater_user_id)},
        )
    ).scalar()
    if active_member is None:
        raise ValidationError("The named rater is not an active member of this workspace.")

    edited = bool(
        (
            await session.execute(
                text(
                    "WITH RECURSIVE memory_chain AS ("
                    "  SELECT id, parent_memory_id FROM memories "
                    "  WHERE id = CAST(:memory_id AS uuid) "
                    "    AND workspace_id = CAST(:workspace_id AS uuid) "
                    "  UNION ALL "
                    "  SELECT parent.id, parent.parent_memory_id FROM memories AS parent "
                    "  JOIN memory_chain AS child ON child.parent_memory_id = parent.id"
                    ") "
                    "SELECT EXISTS ("
                    "  SELECT 1 FROM attribution_edits AS edit "
                    "  JOIN memory_chain AS chain ON chain.id = edit.memory_id "
                    "  WHERE edit.editor_id = CAST(:user_id AS uuid)"
                    ")"
                ),
                {
                    "workspace_id": str(workspace_id),
                    "memory_id": str(memory_id),
                    "user_id": str(rater_user_id),
                },
            )
        ).scalar()
    )
    if source == RatingSource.SELF and not edited:
        raise ValidationError("A self rating requires a recorded edit by the named rater.")
    if source == RatingSource.INDEPENDENT and edited:
        raise ValidationError("An independent rater cannot have edited this memory.")


def _same_rating(existing: SearchRating, body: SearchRatingCreate) -> bool:
    allocation = Decimal(str(body.allocation)) if body.allocation is not None else None
    return (
        existing.search_event_id == body.search_event_id
        and existing.memory_id == body.memory_id
        and existing.rater_user_id == body.rater_user_id
        and existing.rater_pseudonym == body.rater_pseudonym
        and existing.rating_source == body.rating_source.value
        and existing.allocation == allocation
        and existing.exclusion_reason == body.exclusion_reason
    )


async def create_search_rating(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    recorded_by_user_id: uuid.UUID,
    idempotency_key: str,
    body: SearchRatingCreate,
) -> SearchRating:
    key = uuid.UUID(idempotency_key)
    existing = (
        await session.execute(
            select(SearchRating).where(
                SearchRating.workspace_id == workspace_id,
                SearchRating.idempotency_key == key,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if not _same_rating(existing, body):
            raise IdempotencyConflictError(
                "Idempotency-Key was already used for a different pilot rating."
            )
        return existing

    event = (
        await session.execute(
            select(SearchEvent).where(
                SearchEvent.id == body.search_event_id,
                SearchEvent.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if event is None:
        raise ValidationError("Search event does not belong to this workspace.")

    result_rank = _result_rank(event, body.memory_id)
    if result_rank is None:
        raise ValidationError("Memory is not present in the recorded search result snapshot.")

    memory_in_workspace = (
        await session.execute(
            text(
                "SELECT 1 FROM memories "
                "WHERE id = CAST(:memory_id AS uuid) "
                "AND workspace_id = CAST(:workspace_id AS uuid)"
            ),
            {"memory_id": str(body.memory_id), "workspace_id": str(workspace_id)},
        )
    ).scalar()
    if memory_in_workspace is None:
        raise ValidationError("Rated memory does not belong to this workspace.")

    if body.rating_source != RatingSource.EXTERNAL:
        assert body.rater_user_id is not None
        await _validate_named_rater(
            session,
            workspace_id=workspace_id,
            memory_id=body.memory_id,
            rater_user_id=body.rater_user_id,
            source=body.rating_source,
        )

    rating = SearchRating(
        workspace_id=workspace_id,
        search_event_id=body.search_event_id,
        memory_id=body.memory_id,
        result_rank=result_rank,
        rater_user_id=body.rater_user_id,
        rater_pseudonym=body.rater_pseudonym,
        rating_source=body.rating_source.value,
        allocation=Decimal(str(body.allocation)) if body.allocation is not None else None,
        exclusion_reason=body.exclusion_reason,
        idempotency_key=key,
        recorded_by_user_id=recorded_by_user_id,
    )
    try:
        async with session.begin_nested():
            session.add(rating)
            await session.flush()
    except IntegrityError:
        existing = (
            await session.execute(
                select(SearchRating).where(
                    SearchRating.workspace_id == workspace_id,
                    SearchRating.idempotency_key == key,
                )
            )
        ).scalar_one_or_none()
        if existing is None or not _same_rating(existing, body):
            raise IdempotencyConflictError(
                "Idempotency-Key was already used for a different pilot rating."
            ) from None
        return existing
    return rating
