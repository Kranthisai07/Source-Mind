from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sourcemind.api.v1.search import router, search_memories
from sourcemind.core.database import get_db_session
from sourcemind.core.dependencies import (
    AuthenticatedUser,
    get_current_user,
    get_openai_client,
)
from sourcemind.core.exceptions import SourceMindError
from sourcemind.main import _sourcemind_exception_handler
from sourcemind.schemas.memory import SearchRequest


def _user() -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=uuid.uuid4(),
        clerk_id="pilot-user",
        email="pilot@example.com",
    )


@pytest.mark.unit
async def test_search_route_returns_stored_provenance() -> None:
    workspace_id = uuid.uuid4()
    memory_id = uuid.uuid4()
    related_memory_id = uuid.uuid4()
    document_id = uuid.uuid4()
    created_at = datetime(2026, 10, 1, tzinfo=UTC)
    updated_at = datetime(2026, 10, 2, tzinfo=UTC)
    search_result = {
        "results": [
            {
                "id": str(memory_id),
                "document_id": str(document_id),
                "content": "Pilot memory",
                "version": 5,
                "tags": ["pilot", "manual"],
                "category": "decision",
                "confidence_score": 0.84,
                "created_at": created_at,
                "updated_at": updated_at,
                "relation_count": 0,
                "score": 0.7,
                "match_type": "semantic",
            },
            {
                "id": str(related_memory_id),
                "document_id": str(document_id),
                "content": "Related pilot memory",
                "version": 1,
                "tags": ["pilot"],
                "category": "fact",
                "confidence_score": 0.9,
                "created_at": created_at,
                "updated_at": None,
                "relation_count": 2,
                "score": 0.6,
                "match_type": "keyword",
            },
        ],
        "total_found": 2,
    }

    with (
        patch(
            "sourcemind.api.v1.search.require_workspace_permission",
            new=AsyncMock(return_value="member"),
        ),
        patch(
            "sourcemind.api.v1.search.enforce_rate_limit",
            new=AsyncMock(),
        ),
        patch(
            "sourcemind.services.search.hybrid.hybrid_search",
            new=AsyncMock(return_value=search_result),
        ),
    ):
        response = await search_memories(
            body=SearchRequest(query="pilot"),
            db=AsyncMock(),
            current_user=_user(),
            request_id=str(uuid.uuid4()),
            openai_client=object(),
            workspace_id=workspace_id,
        )

    memory = response.results[0].memory
    assert memory.document_id == document_id
    assert memory.version == 5
    assert memory.tags == ["pilot", "manual"]
    assert memory.category == "decision"
    assert memory.confidence_score == 0.84
    assert memory.created_at == created_at
    assert memory.updated_at == updated_at
    assert memory.relation_count == 0
    assert response.results[1].memory.relation_count == 2


@pytest.mark.unit
def test_search_route_rejects_unimplemented_filters_with_http_422() -> None:
    app = FastAPI()
    app.add_exception_handler(SourceMindError, _sourcemind_exception_handler)  # type: ignore[arg-type]
    app.include_router(router, prefix="/v1")
    app.dependency_overrides[get_db_session] = lambda: AsyncMock()
    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_openai_client] = object

    response = TestClient(app).post(
        f"/v1/memories/search?workspace_id={uuid.uuid4()}",
        json={"query": "pilot", "filters": {"tags": ["pilot"]}},
    )

    assert response.status_code == 422
    assert "filters" in response.json()["error"]["message"].lower()
