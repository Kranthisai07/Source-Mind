from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError as PydanticValidationError

from sourcemind.api.v1.pilot import create_pilot_rating
from sourcemind.core.dependencies import AuthenticatedUser, WorkspacePermission
from sourcemind.schemas.memory import MemoryResponse, SearchRequest, SearchResultItem
from sourcemind.schemas.pilot import SearchRatingCreate
from sourcemind.services.pilot_evidence import create_search_rating, record_search_event


def _user() -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=uuid.uuid4(),
        clerk_id="pilot-admin",
        email="pilot-admin@example.com",
    )


@pytest.mark.unit
def test_rating_requires_exactly_one_value_or_exclusion() -> None:
    common = {
        "search_event_id": uuid.uuid4(),
        "memory_id": uuid.uuid4(),
        "rater_pseudonym": "R1",
        "rating_source": "external",
    }
    with pytest.raises(PydanticValidationError, match="exactly one"):
        SearchRatingCreate(**common)
    with pytest.raises(PydanticValidationError, match="exactly one"):
        SearchRatingCreate(**common, allocation=30, exclusion_reason="cannot judge")


@pytest.mark.unit
def test_external_rating_requires_only_a_pseudonym() -> None:
    with pytest.raises(PydanticValidationError, match="require a pseudonym"):
        SearchRatingCreate(
            search_event_id=uuid.uuid4(),
            memory_id=uuid.uuid4(),
            rater_user_id=uuid.uuid4(),
            rating_source="external",
            allocation=25,
        )
    with pytest.raises(PydanticValidationError, match="not a pseudonym"):
        SearchRatingCreate(
            search_event_id=uuid.uuid4(),
            memory_id=uuid.uuid4(),
            rater_user_id=uuid.uuid4(),
            rater_pseudonym="duplicate-identity",
            rating_source="independent",
            allocation=25,
        )


@pytest.mark.unit
async def test_search_event_records_the_exact_ordered_result_snapshot() -> None:
    workspace_id = uuid.uuid4()
    user_id = uuid.uuid4()
    document_id = uuid.uuid4()
    result = SearchResultItem(
        memory=MemoryResponse(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            document_id=document_id,
            content="Pilot result",
            version=2,
            tags=["pilot"],
            category="decision",
            confidence_score=0.9,
            created_at=datetime.now(UTC),
            updated_at=None,
            relation_count=3,
        ),
        score=0.75,
        rank=1,
        match_type="hybrid",
    )
    session = MagicMock()
    session.flush = AsyncMock()

    event = await record_search_event(
        session,
        workspace_id=workspace_id,
        requester_user_id=user_id,
        request_id="request-1",
        request=SearchRequest(query="pilot", limit=5),
        results=[result],
    )

    session.add.assert_called_once_with(event)
    session.flush.assert_awaited_once()
    assert event.result_snapshot == [result.model_dump(mode="json")]
    assert event.memory_ids == [str(result.memory.id)]
    assert event.document_ids == [str(document_id)]
    assert event.algorithm_id == "memory-search-v1:hybrid"


@pytest.mark.unit
async def test_rating_idempotency_replay_precedes_mutable_history_validation() -> None:
    workspace_id = uuid.uuid4()
    body = SearchRatingCreate(
        search_event_id=uuid.uuid4(),
        memory_id=uuid.uuid4(),
        rater_user_id=uuid.uuid4(),
        rating_source="independent",
        allocation=35,
    )
    key = uuid.uuid4()
    existing = MagicMock(
        search_event_id=body.search_event_id,
        memory_id=body.memory_id,
        rater_user_id=body.rater_user_id,
        rater_pseudonym=None,
        rating_source="independent",
        allocation=Decimal("35"),
        exclusion_reason=None,
    )
    query_result = MagicMock()
    query_result.scalar_one_or_none.return_value = existing
    session = MagicMock()
    session.execute = AsyncMock(return_value=query_result)

    replay = await create_search_rating(
        session,
        workspace_id=workspace_id,
        recorded_by_user_id=uuid.uuid4(),
        idempotency_key=str(key),
        body=body,
    )

    assert replay is existing
    session.execute.assert_awaited_once()
    session.add.assert_not_called()


@pytest.mark.unit
async def test_rating_route_is_admin_only_and_forwards_idempotency() -> None:
    workspace_id = uuid.uuid4()
    user = _user()
    body = SearchRatingCreate(
        search_event_id=uuid.uuid4(),
        memory_id=uuid.uuid4(),
        rater_pseudonym="R1",
        rating_source="external",
        allocation=40,
    )
    key = str(uuid.uuid4())
    rating = MagicMock(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        search_event_id=body.search_event_id,
        memory_id=body.memory_id,
        result_rank=1,
        rater_user_id=None,
        rater_pseudonym="R1",
        rating_source="external",
        allocation=Decimal("40"),
        exclusion_reason=None,
        idempotency_key=uuid.UUID(key),
        recorded_by_user_id=user.user_id,
        created_at=datetime.now(UTC),
    )
    require = AsyncMock(return_value="admin")
    creator = AsyncMock(return_value=rating)
    db = MagicMock()
    with (
        patch("sourcemind.api.v1.pilot.require_workspace_permission", new=require),
        patch("sourcemind.services.pilot_evidence.create_search_rating", new=creator),
    ):
        response = await create_pilot_rating(
            workspace_id=workspace_id,
            body=body,
            db=db,
            current_user=user,
            request_id="request-id",
            idempotency_key=key,
        )

    require.assert_awaited_once_with(
        db,
        user.user_id,
        workspace_id,
        WorkspacePermission.ADMINISTER,
    )
    creator.assert_awaited_once_with(
        db,
        workspace_id=workspace_id,
        recorded_by_user_id=user.user_id,
        idempotency_key=key,
        body=body,
    )
    assert response.data.id == rating.id
