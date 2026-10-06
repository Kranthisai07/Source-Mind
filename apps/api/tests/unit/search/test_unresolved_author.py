"""Unresolved GitHub authors are reported, not silently dropped (D-021).

``unresolved_author`` is derived from the DOCUMENT-level artifact link
(``resolved_user_id IS NULL AND source_author IS NOT NULL``), joined by
``document_id``. Every version of a memory shares its document, so the status
survives an edit even though the edit gives the editor an attribution row.
The PostgreSQL behaviour, including the edited-version case, is proven in
tests/integration/test_github_author_links_real_db.py.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

HYBRID = "sourcemind.services.search.hybrid"


def _session(rows):
    session = AsyncMock()
    captured: list[str] = []

    async def execute(stmt, params=None, **_k):
        captured.append(" ".join(str(stmt).split()))
        r = MagicMock()
        r.fetchall.return_value = rows
        return r

    session.execute = AsyncMock(side_effect=execute)
    session.captured = captured
    return session


@pytest.mark.unit
async def test_search_reports_unresolved_author_from_the_document_link() -> None:
    from sourcemind.services.search.hybrid import hybrid_search

    unresolved_id, resolved_id = str(uuid.uuid4()), str(uuid.uuid4())
    session = _session([SimpleNamespace(memory_id=unresolved_id, source_author="alice",
                                        source_tool="github")])
    hits = [
        {"id": unresolved_id, "content": "a", "score": 0.9, "match_type": "semantic"},
        {"id": resolved_id, "content": "b", "score": 0.8, "match_type": "semantic"},
    ]
    with (
        patch(f"{HYBRID}._get_query_embedding", new=AsyncMock(return_value=([0.0], True))),
        patch(f"{HYBRID}._semantic_search", new=AsyncMock(return_value=hits)),
        patch(f"{HYBRID}._keyword_search", new=AsyncMock(return_value=[])),
        patch(f"{HYBRID}._fetch_attributions", new=AsyncMock(return_value={})),
    ):
        result = await hybrid_search(
            session, "q", uuid.uuid4(), include_attribution=True
        )

    by_id = {item["id"]: item for item in result["results"]}
    assert by_id[unresolved_id]["unresolved_author"] == {
        "status": "unresolved",
        "source_author": "alice",
        "source_tool": "github",
    }
    assert by_id[resolved_id]["unresolved_author"] is None
    sql = session.captured[-1]
    assert "artifact_links" in sql
    assert "document_id" in sql
    assert "resolved_user_id IS NULL" in sql
    assert "source_author IS NOT NULL" in sql


@pytest.mark.unit
def test_memory_response_carries_unresolved_author() -> None:
    from sourcemind.schemas.memory import MemoryResponse

    response = MemoryResponse(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        content="c",
        version=2,
        tags=None,
        category=None,
        confidence_score=None,
        created_at=datetime.now(UTC),
        updated_at=None,
        unresolved_author={
            "status": "unresolved",
            "source_author": "alice",
            "source_tool": "github",
        },
    )

    dumped = response.model_dump()
    assert dumped["unresolved_author"] == {
        "status": "unresolved",
        "source_author": "alice",
        "source_tool": "github",
    }


@pytest.mark.unit
def test_memory_response_defaults_to_no_unresolved_author() -> None:
    from sourcemind.schemas.memory import MemoryResponse

    response = MemoryResponse(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        document_id=None,
        content="c",
        version=1,
        tags=None,
        category=None,
        confidence_score=None,
        created_at=datetime.now(UTC),
        updated_at=None,
    )

    assert response.model_dump()["unresolved_author"] is None
