"""The Neo4j query layer must refuse anything that is not scoped to one workspace."""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from sourcemind.core import graph
from sourcemind.core.graph import SCHEMA_STATEMENTS, GraphQueryError, run_read, run_write

WORKSPACE = str(uuid4())
SCOPED_READ = "MATCH (m:Memory {workspace_id: $workspace_id}) RETURN m.id AS id"


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def __aiter__(self):
        async def gen():
            for row in self._rows:
                record = MagicMock()
                record.data.return_value = row
                yield record

        return gen()


class _Session:
    def __init__(self, rows=()):
        self.calls = []
        self._rows = list(rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return None

    async def run(self, query, params=None, **kwargs):
        self.calls.append((query, params, kwargs))
        return _Result(self._rows)


@pytest.mark.unit
async def test_query_without_workspace_predicate_is_refused_before_neo4j() -> None:
    session = _Session()
    with patch.object(graph, "get_graph_session", return_value=session):
        with pytest.raises(GraphQueryError, match="workspace_id"):
            await run_read("MATCH (m:Memory) RETURN m", WORKSPACE)
    assert session.calls == []


@pytest.mark.unit
async def test_workspace_comes_from_the_caller_not_from_params() -> None:
    session = _Session()
    with patch.object(graph, "get_graph_session", return_value=session):
        with pytest.raises(GraphQueryError, match="does not match"):
            await run_read(SCOPED_READ, WORKSPACE, {"workspace_id": str(uuid4())})
    assert session.calls == []


@pytest.mark.unit
async def test_scoped_read_binds_workspace_and_applies_the_timeout() -> None:
    session = _Session(rows=[{"id": "m1"}])
    with patch.object(graph, "get_graph_session", return_value=session):
        rows = await run_read(SCOPED_READ, WORKSPACE)
    assert rows == [{"id": "m1"}]
    _, params, kwargs = session.calls[0]
    assert params["workspace_id"] == WORKSPACE
    assert kwargs["timeout"] > 0


@pytest.mark.unit
async def test_writes_are_guarded_the_same_way() -> None:
    session = _Session()
    scoped = "MERGE (m:Memory {key: $key, workspace_id: $workspace_id})"
    with patch.object(graph, "get_graph_session", return_value=session):
        with pytest.raises(GraphQueryError):
            await run_write("MERGE (m:Memory {key: $key})", WORKSPACE, {"key": "k"})
        await run_write(scoped, WORKSPACE, {"key": "k"})
    assert len(session.calls) == 1


@pytest.mark.unit
def test_get_graph_driver_fails_loudly_when_uninitialised() -> None:
    with patch.object(graph, "_driver", None):
        assert graph.graph_available() is False
        with pytest.raises(RuntimeError, match="not initialized"):
            graph.get_graph_driver()


@pytest.mark.unit
def test_schema_statements_are_idempotent_and_key_on_the_tenant() -> None:
    assert all("IF NOT EXISTS" in statement for statement in SCHEMA_STATEMENTS)
    assert any("memory_key" in statement for statement in SCHEMA_STATEMENTS)
