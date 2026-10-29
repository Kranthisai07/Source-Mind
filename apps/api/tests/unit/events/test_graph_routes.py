"""HTTP behaviour of the graph read endpoints: authorisation first, validation, envelope."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sourcemind.api.v1.graph import router
from sourcemind.core.database import get_db_session
from sourcemind.core.dependencies import AuthenticatedUser, get_current_user
from sourcemind.core.exceptions import MemoryNotFoundError, SourceMindError, WorkspaceNotFoundError
from sourcemind.main import _sourcemind_exception_handler
from sourcemind.schemas.graph import (
    CollaborationResponse,
    LineageResponse,
    OwnersResponse,
    RelatedResponse,
)

WS, MEM = uuid.uuid4(), uuid.uuid4()


def _user() -> AuthenticatedUser:
    return AuthenticatedUser(user_id=uuid.uuid4(), clerk_id="c", email="u@example.com")


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/v1")
    app.add_exception_handler(SourceMindError, _sourcemind_exception_handler)
    app.dependency_overrides[get_db_session] = lambda: AsyncMock()
    app.dependency_overrides[get_current_user] = _user
    return TestClient(app, raise_server_exceptions=False)


def _access(memory: bool = True):
    return (
        patch("sourcemind.api.v1.graph.require_memory_access", new=AsyncMock(return_value=WS))
        if memory
        else patch(
            "sourcemind.api.v1.graph.require_workspace_member", new=AsyncMock(return_value="member")
        )
    )


def _limit():
    return patch("sourcemind.api.v1.graph.enforce_rate_limit", new=AsyncMock())


@pytest.mark.unit
def test_lineage_returns_the_standard_envelope_and_the_serving_backend() -> None:
    answer = LineageResponse(memory_id=MEM, source="graph", versions=[])
    with (
        _access(),
        _limit(),
        patch("sourcemind.services.graph.reads.lineage", new=AsyncMock(return_value=answer)),
    ):
        response = _client().get(f"/v1/memories/{MEM}/lineage")
    body = response.json()
    assert response.status_code == 200
    assert body["data"]["source"] == "graph" and body["data"]["memory_id"] == str(MEM)
    assert set(body["meta"]) == {"request_id", "timestamp", "latency_ms"}


@pytest.mark.unit
@pytest.mark.parametrize("path", ["lineage", "related", "owners"])
def test_a_memory_the_caller_cannot_access_is_a_404_and_the_reader_never_runs(path: str) -> None:
    reader = AsyncMock()
    with (
        patch(
            "sourcemind.api.v1.graph.require_memory_access",
            new=AsyncMock(side_effect=MemoryNotFoundError("Memory not found")),
        ),
        _limit(),
        patch(f"sourcemind.services.graph.reads.{path}", new=reader),
    ):
        response = _client().get(f"/v1/memories/{MEM}/{path}")
    assert response.status_code == 404
    reader.assert_not_awaited()  # no graph query is ever built for an unauthorised caller


@pytest.mark.unit
def test_collaboration_requires_workspace_membership() -> None:
    reader = AsyncMock()
    with (
        patch(
            "sourcemind.api.v1.graph.require_workspace_member",
            new=AsyncMock(side_effect=WorkspaceNotFoundError("nope")),
        ),
        _limit(),
        patch("sourcemind.services.graph.reads.collaboration", new=reader),
    ):
        response = _client().get(f"/v1/workspaces/{WS}/analytics/collaboration-graph")
    assert response.status_code == 404
    reader.assert_not_awaited()


@pytest.mark.unit
def test_the_authorised_workspace_id_is_what_scopes_the_read() -> None:
    reader = AsyncMock(
        return_value=OwnersResponse(memory_id=MEM, source="postgres", depth_served=0, owners=[])
    )
    with _access(), _limit(), patch("sourcemind.services.graph.reads.owners", new=reader):
        _client().get(f"/v1/memories/{MEM}/owners")
    assert reader.await_args.args[1:] == (MEM, WS)  # the workspace came from authorisation


@pytest.mark.unit
@pytest.mark.parametrize(
    "query",
    [
        "depth=0",
        "depth=4",
        "depth=x",
        "limit=0",
        "limit=51",
        "types=bogus",
        "types=extends&types=nope",
    ],
)
def test_bad_query_parameters_are_rejected_before_any_work(query: str) -> None:
    reader = AsyncMock()
    with _access(), _limit(), patch("sourcemind.services.graph.reads.related", new=reader):
        response = _client().get(f"/v1/memories/{MEM}/related?{query}")
    assert response.status_code == 422
    reader.assert_not_awaited()


@pytest.mark.unit
def test_related_passes_validated_parameters_through() -> None:
    reader = AsyncMock(
        return_value=RelatedResponse(
            memory_id=MEM, source="postgres", depth_requested=2, depth_served=1, nodes=[], edges=[]
        )
    )
    with _access(), _limit(), patch("sourcemind.services.graph.reads.related", new=reader):
        response = _client().get(
            f"/v1/memories/{MEM}/related?depth=2&types=extends&types=updates&limit=5"
        )
    assert response.status_code == 200
    kwargs = reader.await_args.kwargs
    assert kwargs == {"depth": 2, "types": ["extends", "updates"], "limit": 5}


@pytest.mark.unit
def test_every_endpoint_is_rate_limited_with_the_analytics_budget() -> None:
    limiter = AsyncMock()
    answers = {
        "lineage": LineageResponse(memory_id=MEM, source="postgres", versions=[]),
        "owners": OwnersResponse(memory_id=MEM, source="postgres", depth_served=0, owners=[]),
        "related": RelatedResponse(
            memory_id=MEM, source="postgres", depth_requested=1, depth_served=1, nodes=[], edges=[]
        ),
    }
    with _access(), patch("sourcemind.api.v1.graph.enforce_rate_limit", new=limiter):
        for name, answer in answers.items():
            with patch(
                f"sourcemind.services.graph.reads.{name}", new=AsyncMock(return_value=answer)
            ):
                assert _client().get(f"/v1/memories/{MEM}/{name}").status_code == 200
    with (
        _access(memory=False),
        patch("sourcemind.api.v1.graph.enforce_rate_limit", new=limiter),
        patch(
            "sourcemind.services.graph.reads.collaboration",
            new=AsyncMock(
                return_value=CollaborationResponse(source="postgres", nodes=[], edges=[])
            ),
        ),
    ):
        assert (
            _client().get(f"/v1/workspaces/{WS}/analytics/collaboration-graph").status_code == 200
        )
    assert limiter.await_count == 4
    assert all(
        call.args[0].value == "analytics" and call.args[2] == WS for call in limiter.await_args_list
    )
