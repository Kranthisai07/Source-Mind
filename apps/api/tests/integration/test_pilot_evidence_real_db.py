"""Real PostgreSQL regressions for append-only pilot search evidence."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.api.v1.search import search_memories
from sourcemind.core.database import set_rls_workspace_context
from sourcemind.core.dependencies import AuthenticatedUser
from sourcemind.core.exceptions import ValidationError
from sourcemind.models.attribution import AttributionEdit
from sourcemind.models.document import Document
from sourcemind.models.memory import Memory
from sourcemind.models.search_evidence import SearchEvent, SearchRating
from sourcemind.models.user import User
from sourcemind.models.workspace import Workspace, WorkspaceMember
from sourcemind.schemas.memory import SearchRequest
from sourcemind.schemas.pilot import SearchRatingCreate
from sourcemind.services.pilot_evidence import create_search_rating


async def _seed_memory(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    label: str,
) -> tuple[Document, Memory]:
    content = f"{label} pilot memory"
    document = Document(
        workspace_id=workspace_id,
        submitter_id=user_id,
        title=f"{label} document",
        source_type="text",
        sha256_hash=hashlib.sha256(f"{label}-{uuid.uuid4()}".encode()).hexdigest(),
        ingestion_status="completed",
        memory_count=1,
    )
    session.add(document)
    await session.flush()
    memory = Memory(
        workspace_id=workspace_id,
        document_id=document.id,
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        tags=["pilot"],
        category="decision",
        confidence_score=0.9,
    )
    session.add(memory)
    await session.flush()
    return document, memory


async def _seed_event(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    requester_user_id: uuid.UUID,
    memory: Memory,
    document: Document,
    query: str,
) -> SearchEvent:
    result = {
        "memory": {
            "id": str(memory.id),
            "workspace_id": str(workspace_id),
            "document_id": str(document.id),
            "content": memory.content,
            "version": memory.version,
            "tags": memory.tags,
            "category": memory.category,
            "confidence_score": memory.confidence_score,
            "created_at": memory.created_at.isoformat(),
            "updated_at": None,
            "attribution": None,
            "unresolved_author": None,
            "relation_count": 0,
        },
        "score": 0.75,
        "rank": 1,
        "match_type": "hybrid",
        "highlight": None,
    }
    event = SearchEvent(
        workspace_id=workspace_id,
        requester_user_id=requester_user_id,
        query=query,
        search_parameters={"query": query, "mode": "hybrid"},
        request_id=f"request-{uuid.uuid4()}",
        result_snapshot=[result],
        memory_ids=[str(memory.id)],
        document_ids=[str(document.id)],
        algorithm_id="memory-search-v1:hybrid",
    )
    session.add(event)
    await session.flush()
    return event


async def _seed_workspace(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    owner_id: uuid.UUID,
    label: str,
) -> Workspace:
    workspace_id = uuid.uuid4()
    await set_rls_workspace_context(session, workspace_id)
    workspace = Workspace(
        id=workspace_id,
        organization_id=organization_id,
        created_by_user_id=owner_id,
        name=label,
        slug=f"{label.lower()}-{uuid.uuid4().hex[:8]}",
    )
    session.add(workspace)
    await session.flush()
    session.add(
        WorkspaceMember(workspace_id=workspace_id, user_id=owner_id, role="owner")
    )
    await session.flush()
    return workspace


@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_evidence_is_force_rls_scoped_and_append_only(
    db_session: AsyncSession,
    test_org,
    test_user,
    test_workspace,
) -> None:
    document_a, memory_a = await _seed_memory(
        db_session,
        workspace_id=test_workspace.id,
        user_id=test_user.id,
        label="workspace-a",
    )
    event_a = await _seed_event(
        db_session,
        workspace_id=test_workspace.id,
        requester_user_id=test_user.id,
        memory=memory_a,
        document=document_a,
        query="workspace a",
    )

    other_workspace = await _seed_workspace(
        db_session,
        organization_id=test_org.id,
        owner_id=test_user.id,
        label="Other Evidence Workspace",
    )
    document_b, memory_b = await _seed_memory(
        db_session,
        workspace_id=other_workspace.id,
        user_id=test_user.id,
        label="workspace-b",
    )
    event_b = await _seed_event(
        db_session,
        workspace_id=other_workspace.id,
        requester_user_id=test_user.id,
        memory=memory_b,
        document=document_b,
        query="workspace b",
    )

    await set_rls_workspace_context(db_session, test_workspace.id)
    visible_ids = set((await db_session.scalars(select(SearchEvent.id))).all())
    assert visible_ids == {event_a.id}
    assert event_b.id not in visible_ids

    flags = (
        await db_session.execute(
            text(
                "SELECT relname, relrowsecurity, relforcerowsecurity "
                "FROM pg_class WHERE oid IN "
                "('public.search_events'::regclass, 'public.search_ratings'::regclass)"
            )
        )
    ).all()
    assert {(row.relname, row.relrowsecurity, row.relforcerowsecurity) for row in flags} == {
        ("search_events", True, True),
        ("search_ratings", True, True),
    }
    triggers = set(
        (
            await db_session.scalars(
                text(
                    "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal "
                    "AND tgrelid IN "
                    "('public.search_events'::regclass, 'public.search_ratings'::regclass)"
                )
            )
        ).all()
    )
    assert triggers == {"search_events_append_only", "search_ratings_append_only"}
    for table in ("search_events", "search_ratings"):
        assert await db_session.scalar(
            text("SELECT has_table_privilege(current_user, :table, 'SELECT')"),
            {"table": table},
        )
        assert await db_session.scalar(
            text("SELECT has_table_privilege(current_user, :table, 'INSERT')"),
            {"table": table},
        )
        assert not await db_session.scalar(
            text("SELECT has_table_privilege(current_user, :table, 'UPDATE')"),
            {"table": table},
        )
        assert not await db_session.scalar(
            text("SELECT has_table_privilege(current_user, :table, 'DELETE')"),
            {"table": table},
        )

    with pytest.raises(DBAPIError, match="permission denied"):
        async with db_session.begin_nested():
            await db_session.execute(
                text("UPDATE search_events SET query = 'changed' WHERE id = :event_id"),
                {"event_id": event_a.id},
            )
    with pytest.raises(DBAPIError, match="permission denied"):
        async with db_session.begin_nested():
            await db_session.execute(
                text("DELETE FROM search_events WHERE id = :event_id"),
                {"event_id": event_a.id},
            )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_successful_search_event_matches_the_returned_response(
    db_session: AsyncSession,
    test_user,
    test_workspace,
) -> None:
    document, memory = await _seed_memory(
        db_session,
        workspace_id=test_workspace.id,
        user_id=test_user.id,
        label="equivalence",
    )
    created_at = memory.created_at or datetime.now(UTC)
    search_result = {
        "results": [
            {
                "id": str(memory.id),
                "document_id": str(document.id),
                "content": memory.content,
                "version": memory.version,
                "tags": memory.tags,
                "category": memory.category,
                "confidence_score": memory.confidence_score,
                "created_at": created_at,
                "updated_at": memory.updated_at,
                "relation_count": 0,
                "score": 0.75,
                "match_type": "hybrid",
            }
        ],
        "total_found": 1,
    }
    user = AuthenticatedUser(
        user_id=test_user.id,
        clerk_id=test_user.clerk_id,
        email=test_user.email,
    )
    with (
        patch("sourcemind.api.v1.search.enforce_rate_limit", new=AsyncMock()),
        patch(
            "sourcemind.services.search.hybrid.hybrid_search",
            new=AsyncMock(return_value=search_result),
        ),
        patch(
            "sourcemind.api.v1.search.get_settings",
            return_value=SimpleNamespace(pilot_search_evidence_enabled=True),
        ),
    ):
        response = await search_memories(
            body=SearchRequest(query="pilot equivalence"),
            db=db_session,
            current_user=user,
            request_id="request-equivalence",
            openai_client=object(),
            workspace_id=test_workspace.id,
        )

    assert response.search_event_id is not None
    event = await db_session.get(SearchEvent, response.search_event_id)
    assert event is not None
    assert event.request_id == "request-equivalence"
    assert event.algorithm_id == "memory-search-v1:hybrid"
    assert event.search_parameters == SearchRequest(query="pilot equivalence").model_dump(
        mode="json"
    )
    assert event.result_snapshot == [
        result.model_dump(mode="json") for result in response.results
    ]
    assert event.memory_ids == [str(memory.id)]
    assert event.document_ids == [str(document.id)]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rating_validation_uses_recorded_edit_history(
    db_session: AsyncSession,
    test_user,
    test_workspace,
) -> None:
    document, memory = await _seed_memory(
        db_session,
        workspace_id=test_workspace.id,
        user_id=test_user.id,
        label="rating-validation",
    )
    event = await _seed_event(
        db_session,
        workspace_id=test_workspace.id,
        requester_user_id=test_user.id,
        memory=memory,
        document=document,
        query="rating validation",
    )
    rater = User(
        clerk_id=f"pilot-rater-{uuid.uuid4().hex}",
        email=f"pilot-rater-{uuid.uuid4().hex}@example.com",
    )
    db_session.add(rater)
    await db_session.flush()
    db_session.add(
        WorkspaceMember(
            workspace_id=test_workspace.id,
            user_id=rater.id,
            role="member",
        )
    )
    await db_session.flush()

    independent = SearchRatingCreate(
        search_event_id=event.id,
        memory_id=memory.id,
        rater_user_id=rater.id,
        rating_source="independent",
        allocation=35,
    )
    key = str(uuid.uuid4())
    first = await create_search_rating(
        db_session,
        workspace_id=test_workspace.id,
        recorded_by_user_id=test_user.id,
        idempotency_key=key,
        body=independent,
    )
    db_session.add(
        AttributionEdit(
            memory_id=memory.id,
            editor_id=rater.id,
            content_before=memory.content,
            content_after=f"{memory.content} corrected",
            edit_position=1,
            action_type="edit",
        )
    )
    await db_session.flush()
    replay = await create_search_rating(
        db_session,
        workspace_id=test_workspace.id,
        recorded_by_user_id=test_user.id,
        idempotency_key=key,
        body=independent,
    )
    assert replay.id == first.id

    with pytest.raises(ValidationError, match="independent rater cannot have edited"):
        await create_search_rating(
            db_session,
            workspace_id=test_workspace.id,
            recorded_by_user_id=test_user.id,
            idempotency_key=str(uuid.uuid4()),
            body=independent,
        )

    self_rating = await create_search_rating(
        db_session,
        workspace_id=test_workspace.id,
        recorded_by_user_id=test_user.id,
        idempotency_key=str(uuid.uuid4()),
        body=SearchRatingCreate(
            search_event_id=event.id,
            memory_id=memory.id,
            rater_user_id=rater.id,
            rating_source="self",
            allocation=35,
        ),
    )
    external = await create_search_rating(
        db_session,
        workspace_id=test_workspace.id,
        recorded_by_user_id=test_user.id,
        idempotency_key=str(uuid.uuid4()),
        body=SearchRatingCreate(
            search_event_id=event.id,
            memory_id=memory.id,
            rater_pseudonym="external-01",
            rating_source="external",
            exclusion_reason="insufficient context",
        ),
    )
    assert self_rating.result_rank == external.result_rank == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rating_rejects_cross_workspace_event_and_memory(
    db_session: AsyncSession,
    test_org,
    test_user,
    test_workspace,
) -> None:
    other_workspace = await _seed_workspace(
        db_session,
        organization_id=test_org.id,
        owner_id=test_user.id,
        label="Other Rating Workspace",
    )
    document_b, memory_b = await _seed_memory(
        db_session,
        workspace_id=other_workspace.id,
        user_id=test_user.id,
        label="cross-workspace",
    )
    event_b = await _seed_event(
        db_session,
        workspace_id=other_workspace.id,
        requester_user_id=test_user.id,
        memory=memory_b,
        document=document_b,
        query="other workspace",
    )

    await set_rls_workspace_context(db_session, test_workspace.id)
    external_for_b = SearchRatingCreate(
        search_event_id=event_b.id,
        memory_id=memory_b.id,
        rater_pseudonym="external-02",
        rating_source="external",
        allocation=25,
    )
    with pytest.raises(ValidationError, match="event does not belong"):
        await create_search_rating(
            db_session,
            workspace_id=test_workspace.id,
            recorded_by_user_id=test_user.id,
            idempotency_key=str(uuid.uuid4()),
            body=external_for_b,
        )

    fake_event_a = SearchEvent(
        workspace_id=test_workspace.id,
        requester_user_id=test_user.id,
        query="tampered snapshot",
        search_parameters={"query": "tampered snapshot"},
        request_id="tampered",
        result_snapshot=[
            {"memory": {"id": str(memory_b.id)}, "score": 1.0, "rank": 1}
        ],
        memory_ids=[str(memory_b.id)],
        document_ids=[str(document_b.id)],
        algorithm_id="memory-search-v1:hybrid",
    )
    db_session.add(fake_event_a)
    await db_session.flush()
    with pytest.raises(ValidationError, match="memory does not belong"):
        await create_search_rating(
            db_session,
            workspace_id=test_workspace.id,
            recorded_by_user_id=test_user.id,
            idempotency_key=str(uuid.uuid4()),
            body=SearchRatingCreate(
                search_event_id=fake_event_a.id,
                memory_id=memory_b.id,
                rater_pseudonym="external-02",
                rating_source="external",
                allocation=25,
            ),
        )

    assert not (
        await db_session.scalars(
            select(SearchRating).where(SearchRating.memory_id == memory_b.id)
        )
    ).all()
