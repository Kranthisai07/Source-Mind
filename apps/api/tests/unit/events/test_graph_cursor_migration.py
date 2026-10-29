"""Migration 20261029_0016 (graph_publication_cursor) and the relay's cursor upsert."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sourcemind.events import relay
from tests.unit.events.fakes import RoutedSession

VERSIONS = Path(__file__).resolve().parents[3] / "alembic" / "versions"


def _migration():
    (path,) = sorted(VERSIONS.glob("*_0016_graph_publication_cursor.py"))
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _online(migration, owner: str = "sourcemind_owner"):
    migration.op = MagicMock()
    migration.context = MagicMock()
    migration.context.is_offline_mode.return_value = False

    def execute(statement, params=None):
        result = MagicMock()
        sql = str(statement)
        result.scalar.return_value = 1 if "pg_roles" in sql else owner
        return result

    migration.op.get_bind.return_value.execute.side_effect = execute
    return migration


def _sql(migration) -> list[str]:
    return [str(call.args[0]) for call in migration.op.execute.call_args_list]


@pytest.mark.unit
def test_revision_chain_follows_0015() -> None:
    migration = _migration()
    assert migration.revision == "20261029_0016"
    assert migration.down_revision == "20261029_0015"


@pytest.mark.unit
def test_upgrade_aborts_before_any_ddl_without_the_runtime_role(monkeypatch) -> None:
    migration = _online(_migration())
    monkeypatch.delenv("SOURCEMIND_RUNTIME_ROLE", raising=False)
    with pytest.raises(RuntimeError, match="SOURCEMIND_RUNTIME_ROLE"):
        migration.upgrade()
    migration.op.execute.assert_not_called()


@pytest.mark.unit
def test_the_runtime_role_may_only_read_the_cursor(monkeypatch) -> None:
    migration = _online(_migration())
    monkeypatch.setenv("SOURCEMIND_RUNTIME_ROLE", "sourcemind_runtime")
    monkeypatch.delenv("SOURCEMIND_GRAPH_RELAY_ROLE", raising=False)
    migration.upgrade()
    sql = _sql(migration)
    assert any(
        'GRANT SELECT ON TABLE graph_publication_cursor TO "sourcemind_runtime"' in s for s in sql
    )
    assert not any("INSERT" in s and "GRANT" in s for s in sql)
    assert any(
        'REVOKE ALL ON TABLE graph_publication_cursor FROM "sourcemind_runtime"' in s for s in sql
    )


@pytest.mark.unit
def test_the_relay_role_may_upsert_but_only_its_two_columns(monkeypatch) -> None:
    migration = _online(_migration())
    monkeypatch.setenv("SOURCEMIND_RUNTIME_ROLE", "sourcemind_runtime")
    monkeypatch.setenv("SOURCEMIND_GRAPH_RELAY_ROLE", "sourcemind_graph_relay")
    migration.upgrade()
    sql = _sql(migration)
    assert any(
        'GRANT SELECT, INSERT ON TABLE graph_publication_cursor TO "sourcemind_graph_relay"' in s
        for s in sql
    )
    assert any(
        "GRANT UPDATE (published_seq, updated_at) ON TABLE graph_publication_cursor" in s
        for s in sql
    )
    assert not any("DELETE" in s and "GRANT" in s for s in sql)


@pytest.mark.unit
def test_downgrade_just_drops_the_table() -> None:
    migration = _migration()
    migration.op = MagicMock()
    migration.downgrade()
    assert "DROP TABLE IF EXISTS graph_publication_cursor" in str(
        migration.op.execute.call_args.args[0]
    )


@pytest.mark.unit
async def test_the_relay_advances_the_cursor_in_the_same_transaction_as_marking_rows() -> None:
    conn = RoutedSession()
    await relay.mark_published(conn, [4, 5, 6])
    marks = [(sql, p) for sql, p in conn.statements]
    assert marks[0][0].startswith("UPDATE graph_outbox SET published_at")
    assert marks[1][0].startswith("INSERT INTO graph_publication_cursor")
    assert "GREATEST(graph_publication_cursor.published_seq" in marks[1][0]  # never moves backwards
    assert marks[0][1] == marks[1][1] == {"seqs": [4, 5, 6]}
    assert conn.commits == 0  # the caller commits once, after both statements
