from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sourcemind.core.dependencies import AuthenticatedUser, WorkspacePermission


def _session(rows_by_table: dict[str, list[dict[str, object]]]):
    calls: list[tuple[str, dict[str, object]]] = []

    async def execute(statement, params):
        sql = " ".join(str(statement).split())
        calls.append((sql, dict(params)))
        table = next(name for name in rows_by_table if f"FROM {name}" in sql)
        workspace_id = params["workspace_id"]
        rows = [
            {key: value for key, value in row.items() if key != "_workspace_id"}
            for row in rows_by_table[table]
            if row["_workspace_id"] == workspace_id
        ]
        result = MagicMock()
        result.mappings.return_value.all.return_value = rows
        return result

    session = MagicMock()
    session.execute = AsyncMock(side_effect=execute)
    session.calls = calls
    return session


@pytest.mark.unit
async def test_pilot_export_scopes_every_dataset_to_one_workspace() -> None:
    from sourcemind.services.pilot_export import export_workspace_pilot_data

    workspace_id = uuid.uuid4()
    other_workspace_id = uuid.uuid4()
    session = _session(
        {
            "documents": [
                {"id": "document-a", "_workspace_id": str(workspace_id)},
                {"id": "document-b", "_workspace_id": str(other_workspace_id)},
            ],
            "memories": [
                {"id": "memory-a", "_workspace_id": str(workspace_id)},
                {"id": "memory-b", "_workspace_id": str(other_workspace_id)},
            ],
            "attributions": [
                {"id": "attribution-a", "_workspace_id": str(workspace_id)},
                {"id": "attribution-b", "_workspace_id": str(other_workspace_id)},
            ],
            "attribution_edits": [
                {"id": "edit-a", "_workspace_id": str(workspace_id)},
                {"id": "edit-b", "_workspace_id": str(other_workspace_id)},
            ],
        }
    )

    export = await export_workspace_pilot_data(session, workspace_id)

    assert export["workspace_id"] == str(workspace_id)
    assert export["search_events"] == []
    assert export["search_ratings"] == []
    assert set(export["unavailable_datasets"]) == {"search_events", "search_ratings"}
    assert export["documents"] == [{"id": "document-a"}]
    assert export["memories"] == [{"id": "memory-a"}]
    assert export["versions"] == [{"id": "memory-a"}]
    assert export["attributions"] == [{"id": "attribution-a"}]
    assert export["attribution_edits"] == [{"id": "edit-a"}]
    assert len(session.calls) == 5
    for sql, params in session.calls:
        assert "workspace_id = CAST(:workspace_id AS uuid)" in sql
        assert params == {"workspace_id": str(workspace_id)}


@pytest.mark.unit
async def test_pilot_export_route_requires_administer_permission() -> None:
    from sourcemind.api.v1.pilot import get_pilot_export

    workspace_id = uuid.uuid4()
    user = AuthenticatedUser(
        user_id=uuid.uuid4(),
        clerk_id="admin",
        email="admin@example.com",
    )
    require = AsyncMock(return_value="admin")
    db = MagicMock()
    exporter = AsyncMock(
        return_value={
            "workspace_id": str(workspace_id),
            "documents": [],
            "memories": [],
            "versions": [],
            "attributions": [],
            "attribution_edits": [],
            "search_events": [],
            "search_ratings": [],
            "unavailable_datasets": {},
        }
    )
    with (
        patch("sourcemind.api.v1.pilot.require_workspace_permission", new=require),
        patch("sourcemind.services.pilot_export.export_workspace_pilot_data", new=exporter),
    ):
        response = await get_pilot_export(
            workspace_id=workspace_id,
            db=db,
            current_user=user,
            request_id="request-id",
        )

    require.assert_awaited_once_with(
        db,
        user.user_id,
        workspace_id,
        WorkspacePermission.ADMINISTER,
    )
    exporter.assert_awaited_once_with(db, workspace_id)
    assert response.data["workspace_id"] == str(workspace_id)
