"""Migration 20261029_0015: graph_outbox grants and rollback guard.

The outbox is protected by grants rather than row-level security so the relay,
which spans every workspace, never needs a policy bypass: the runtime role may
only INSERT, and an optional relay role may only SELECT and UPDATE
(published_at). These tests drive the migration against a mocked ``op``; the
real-database behaviour is covered by the graph integration tests.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest

API_ROOT = Path(__file__).resolve().parents[3]
VERSIONS = API_ROOT / "alembic" / "versions"


def _migration():
    matches = sorted(VERSIONS.glob("*_0015_graph_outbox.py"))
    assert len(matches) == 1, "expected exactly one 0015 graph_outbox revision"
    spec = importlib.util.spec_from_file_location(matches[0].stem, matches[0])
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _online(migration, *, role_exists: bool = True, owner: str = "sourcemind_owner"):
    migration.op = MagicMock()
    migration.context = MagicMock()
    migration.context.is_offline_mode.return_value = False
    bind = migration.op.get_bind.return_value

    def execute(statement, params=None):
        result = MagicMock()
        sql = str(statement)
        if "pg_roles" in sql:
            result.scalar.return_value = 1 if role_exists else None
        elif "pg_tables" in sql:
            result.scalar.return_value = owner
        else:
            result.scalar.return_value = 0
        return result

    bind.execute.side_effect = execute
    return migration


def _sql(migration) -> list[str]:
    return [str(call.args[0]) for call in migration.op.execute.call_args_list]


@pytest.mark.unit
def test_revision_chain_follows_0014() -> None:
    migration = _migration()
    assert migration.revision == "20261029_0015"
    assert migration.down_revision == "20261016_0014"


@pytest.mark.unit
@pytest.mark.parametrize("value", [None, "", "  "])
def test_upgrade_aborts_before_any_ddl_without_the_runtime_role(monkeypatch, value) -> None:
    migration = _online(_migration())
    monkeypatch.delenv("SOURCEMIND_GRAPH_RELAY_ROLE", raising=False)
    if value is None:
        monkeypatch.delenv("SOURCEMIND_RUNTIME_ROLE", raising=False)
    else:
        monkeypatch.setenv("SOURCEMIND_RUNTIME_ROLE", value)

    with pytest.raises(RuntimeError, match="SOURCEMIND_RUNTIME_ROLE"):
        migration.upgrade()

    migration.op.execute.assert_not_called()


@pytest.mark.unit
def test_runtime_role_can_only_insert_and_use_the_sequence(monkeypatch) -> None:
    migration = _online(_migration())
    monkeypatch.setenv("SOURCEMIND_RUNTIME_ROLE", "sourcemind_runtime")
    monkeypatch.delenv("SOURCEMIND_GRAPH_RELAY_ROLE", raising=False)

    migration.upgrade()

    sql = _sql(migration)
    assert any("GRANT INSERT ON TABLE graph_outbox" in s for s in sql)
    assert any("GRANT USAGE ON SEQUENCE graph_outbox_seq_seq" in s for s in sql)
    # Default privileges would otherwise hand the runtime role full DML.
    assert any('REVOKE ALL ON TABLE graph_outbox FROM "sourcemind_runtime"' in s for s in sql)
    assert not any("SELECT" in s and "GRANT" in s for s in sql)


@pytest.mark.unit
def test_relay_grants_are_skipped_when_the_role_is_not_named(monkeypatch) -> None:
    migration = _online(_migration())
    monkeypatch.setenv("SOURCEMIND_RUNTIME_ROLE", "sourcemind_runtime")
    monkeypatch.delenv("SOURCEMIND_GRAPH_RELAY_ROLE", raising=False)

    migration.upgrade()

    assert not any("relay" in s.lower() for s in _sql(migration))


@pytest.mark.unit
def test_relay_role_gets_select_and_update_of_published_at_only(monkeypatch) -> None:
    migration = _online(_migration())
    monkeypatch.setenv("SOURCEMIND_RUNTIME_ROLE", "sourcemind_runtime")
    monkeypatch.setenv("SOURCEMIND_GRAPH_RELAY_ROLE", "sourcemind_graph_relay")

    migration.upgrade()

    sql = _sql(migration)
    assert any('GRANT SELECT ON TABLE graph_outbox TO "sourcemind_graph_relay"' in s for s in sql)
    assert any(
        'GRANT UPDATE (published_at) ON TABLE graph_outbox TO "sourcemind_graph_relay"' in s
        for s in sql
    )
    assert not any("INSERT" in s and "sourcemind_graph_relay" in s for s in sql)


@pytest.mark.unit
def test_a_named_relay_role_that_does_not_exist_aborts(monkeypatch) -> None:
    migration = _online(_migration(), role_exists=False)
    monkeypatch.setenv("SOURCEMIND_RUNTIME_ROLE", "sourcemind_runtime")
    with pytest.raises(RuntimeError, match="does not exist"):
        migration.upgrade()
    migration.op.execute.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("role", ["bad-role;DROP", "1abc", "has space"])
def test_role_names_are_validated_before_being_interpolated(monkeypatch, role) -> None:
    migration = _online(_migration())
    monkeypatch.setenv("SOURCEMIND_RUNTIME_ROLE", "sourcemind_runtime")
    monkeypatch.setenv("SOURCEMIND_GRAPH_RELAY_ROLE", role)
    with pytest.raises(RuntimeError, match="identifier"):
        migration.upgrade()
    migration.op.execute.assert_not_called()


@pytest.mark.unit
def test_payload_column_exists_but_the_table_has_no_content_columns() -> None:
    source = (VERSIONS / "20261029_0015_graph_outbox.py").read_text(encoding="utf-8")
    for forbidden in ("content", "embedding", "email", "display_name"):
        assert f"\n            {forbidden} " not in source


@pytest.mark.unit
def test_downgrade_refuses_to_drop_unpublished_events() -> None:
    migration = _migration()
    migration.op = MagicMock()
    migration.context = MagicMock()
    migration.context.is_offline_mode.return_value = False
    migration.op.get_bind.return_value.execute.return_value.scalar.return_value = 3

    with pytest.raises(RuntimeError, match="not yet published"):
        migration.downgrade()

    migration.op.execute.assert_not_called()


@pytest.mark.unit
def test_downgrade_drops_the_table_once_everything_is_published() -> None:
    migration = _migration()
    migration.op = MagicMock()
    migration.context = MagicMock()
    migration.context.is_offline_mode.return_value = False
    migration.op.get_bind.return_value.execute.return_value.scalar.return_value = 0

    migration.downgrade()

    statements = [str(c.args[0]) for c in migration.op.execute.call_args_list]
    assert any("DROP TABLE IF EXISTS graph_outbox" in s for s in statements)
