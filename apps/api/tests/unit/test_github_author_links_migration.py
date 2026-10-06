"""Migration 20261005_0010: github_author_links grants, RLS and rollback guard.

No migration before this one grants to a runtime role; CI masked that with
ALTER DEFAULT PRIVILEGES while production uses an exact table allowlist. This
migration therefore grants explicitly, to a role named by the REQUIRED
environment variable SOURCEMIND_RUNTIME_ROLE, and aborts before any DDL when
the variable is unset or the role does not exist.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

API_ROOT = Path(__file__).resolve().parents[2]
VERSIONS = API_ROOT / "alembic" / "versions"


def _load(name: str):
    path = VERSIONS / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration():
    matches = sorted(VERSIONS.glob("*_0010_github_author_links.py"))
    assert len(matches) == 1, "expected exactly one 0010 github_author_links revision"
    return _load(matches[0].name)


def _online(migration, *, role_exists: bool = True):
    migration.op = MagicMock()
    migration.context = MagicMock()
    migration.context.is_offline_mode.return_value = False
    bind = migration.op.get_bind.return_value
    bind.execute.return_value.scalar.return_value = 1 if role_exists else None
    return migration


@pytest.mark.unit
def test_revision_chain_follows_0009() -> None:
    migration = _migration()

    assert migration.revision == "20261005_0010"
    assert migration.down_revision == "20260916_0009"


@pytest.mark.unit
@pytest.mark.parametrize("value", [None, "", "  "])
def test_upgrade_aborts_before_any_ddl_without_the_runtime_role(monkeypatch, value) -> None:
    migration = _online(_migration())
    if value is None:
        monkeypatch.delenv("SOURCEMIND_RUNTIME_ROLE", raising=False)
    else:
        monkeypatch.setenv("SOURCEMIND_RUNTIME_ROLE", value)

    with pytest.raises(RuntimeError, match="SOURCEMIND_RUNTIME_ROLE"):
        migration.upgrade()

    migration.op.execute.assert_not_called()
    migration.op.create_table.assert_not_called()


@pytest.mark.unit
def test_upgrade_aborts_when_the_role_does_not_exist(monkeypatch) -> None:
    migration = _online(_migration(), role_exists=False)
    monkeypatch.setenv("SOURCEMIND_RUNTIME_ROLE", "sourcemind_runtime")

    with pytest.raises(RuntimeError, match="does not exist"):
        migration.upgrade()

    migration.op.execute.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("bad", ['x"; DROP TABLE users; --', "1role", "a b", "role;"])
def test_upgrade_rejects_role_names_that_are_not_plain_identifiers(monkeypatch, bad) -> None:
    migration = _online(_migration())
    monkeypatch.setenv("SOURCEMIND_RUNTIME_ROLE", bad)

    with pytest.raises(RuntimeError, match="SOURCEMIND_RUNTIME_ROLE"):
        migration.upgrade()

    migration.op.execute.assert_not_called()


@pytest.mark.unit
def test_grant_function_grants_exactly_four_privileges_and_revokes_public() -> None:
    migration = _migration()
    executed: list[str] = []

    migration.grant_runtime_privileges(executed.append, "sourcemind_runtime")

    normalized = [" ".join(s.split()) for s in executed]
    assert "REVOKE ALL ON TABLE github_author_links FROM PUBLIC" in normalized
    assert 'REVOKE ALL ON TABLE github_author_links FROM "sourcemind_runtime"' in normalized
    grants = [s for s in normalized if s.startswith("GRANT")]
    assert grants == [
        'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE github_author_links TO "sourcemind_runtime"'
    ]
    # Revokes come first so the end state is exact whatever defaults granted.
    assert normalized.index(grants[0]) == len(normalized) - 1
    for extra in ("TRUNCATE", "REFERENCES", "TRIGGER", "SEQUENCE", "ALL PRIVILEGES"):
        assert extra not in grants[0]


@pytest.mark.unit
def test_upgrade_creates_table_with_forced_rls_and_explicit_grants(monkeypatch) -> None:
    migration = _online(_migration())
    monkeypatch.setenv("SOURCEMIND_RUNTIME_ROLE", "sourcemind_runtime")

    migration.upgrade()

    statements = [" ".join(c.args[0].split()) for c in migration.op.execute.call_args_list]
    ddl = " ".join(statements)
    assert "CREATE TABLE github_author_links" in ddl
    assert "github_user_id bigint NOT NULL" in ddl
    assert "CHECK (github_user_id > 0)" in ddl
    assert "UNIQUE (workspace_id, github_user_id)" in ddl
    assert "ON github_author_links (workspace_id, user_id)" in ddl
    assert "ALTER TABLE github_author_links ENABLE ROW LEVEL SECURITY" in statements
    assert "ALTER TABLE github_author_links FORCE ROW LEVEL SECURITY" in statements
    assert (
        'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE github_author_links TO "sourcemind_runtime"'
        in statements
    )
    # The grant comes after the table exists.
    create_at = next(i for i, s in enumerate(statements) if s.startswith("CREATE TABLE"))
    grant_at = statements.index(
        'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE github_author_links TO "sourcemind_runtime"'
    )
    assert grant_at > create_at


@pytest.mark.unit
def test_policy_is_the_0006_active_access_expression() -> None:
    migration = _migration()
    security_foundation = _load("20260908_0006_security_foundation.py")

    expected = security_foundation._active_access("github_author_links.workspace_id")
    assert " ".join(migration._active_access("github_author_links.workspace_id").split()) == (
        " ".join(expected.split())
    )


@pytest.mark.unit
def test_downgrade_drops_the_table_when_rollback_is_compatible() -> None:
    migration = _online(_migration())
    migration.context.get_revision_argument.return_value = "20260916_0009"

    migration.downgrade()

    statements = [" ".join(c.args[0].split()) for c in migration.op.execute.call_args_list]
    assert statements == ["DROP TABLE IF EXISTS github_author_links"]
    migration.op.get_bind.assert_not_called()


@pytest.mark.unit
def test_downgrade_to_0005_refuses_before_dropping_anything() -> None:
    """transaction_per_migration=True: without this guard 0010's drop would
    commit before 0009's guard refused the rollback (see D-013)."""
    migration = _online(_migration())
    migration.context.get_revision_argument.return_value = "20250817_0005"
    migration.op.get_bind.return_value.execute.side_effect = IntegrityError(
        "preflight", {}, Exception("check violation")
    )

    with pytest.raises(RuntimeError, match="connector types unsupported by 0005"):
        migration.downgrade()

    migration.op.execute.assert_not_called()


def _policies(statements: list[str]) -> dict[str, str]:
    return {
        s.split()[2]: s
        for s in statements
        if s.startswith("CREATE POLICY") and " ON github_author_links " in s
    }


@pytest.mark.unit
def test_policies_are_per_command_with_admin_only_writes(monkeypatch) -> None:
    migration = _online(_migration())
    monkeypatch.setenv("SOURCEMIND_RUNTIME_ROLE", "sourcemind_runtime")

    migration.upgrade()

    statements = [" ".join(c.args[0].split()) for c in migration.op.execute.call_args_list]
    policies = _policies(statements)
    assert set(policies) == {
        "sm_github_author_links_select",
        "sm_github_author_links_insert",
        "sm_github_author_links_update",
        "sm_github_author_links_delete",
    }
    member = " ".join(migration._active_access("github_author_links.workspace_id").split())
    admin = " ".join(migration._admin_access("github_author_links.workspace_id").split())
    assert "access_membership.role IN ('owner', 'admin')" in admin
    assert member in admin.replace(" AND access_membership.role IN ('owner', 'admin')", "")

    def clause(sql: str, keyword: str) -> str:
        # "... USING (<expr>)" / "... WITH CHECK (<expr>)"
        body = sql.split(f"{keyword} (", 1)[1]
        for other in (" WITH CHECK (",):
            body = body.split(other, 1)[0]
        return body.rsplit(")", 1)[0].strip()

    select = policies["sm_github_author_links_select"]
    insert = policies["sm_github_author_links_insert"]
    update = policies["sm_github_author_links_update"]
    delete = policies["sm_github_author_links_delete"]
    assert " FOR SELECT " in select and clause(select, "USING") == member
    assert " FOR INSERT " in insert and clause(insert, "WITH CHECK") == admin
    assert "USING" not in insert
    # UPDATE USING stays member-level so SELECT ... FOR SHARE (which evaluates
    # the UPDATE policy's USING) works for the worker running as a member;
    # WITH CHECK makes the actual change admin-only.
    assert " FOR UPDATE " in update
    assert clause(update, "USING") == member
    assert clause(update, "WITH CHECK") == admin
    assert " FOR DELETE " in delete and clause(delete, "USING") == admin
    assert "WITH CHECK" not in delete
    # The single FOR ALL tenant policy is gone.
    assert not [s for s in statements if "sm_tenant_isolation" in s]


@pytest.mark.unit
def test_grant_is_skipped_when_the_runtime_role_owns_the_table() -> None:
    """REVOKE ALL FROM the owner would strip its own TRUNCATE/TRIGGER/REFERENCES."""
    migration = _migration()
    executed: list[str] = []

    migration.grant_runtime_privileges(executed.append, "sourcemind", table_owner="sourcemind")

    assert executed == ["REVOKE ALL ON TABLE github_author_links FROM PUBLIC"]


@pytest.mark.unit
def test_upgrade_passes_the_actual_table_owner_to_the_grant(monkeypatch) -> None:
    migration = _online(_migration())
    monkeypatch.setenv("SOURCEMIND_RUNTIME_ROLE", "sourcemind")
    bind = migration.op.get_bind.return_value

    def execute(stmt, params=None):
        result = MagicMock()
        sql = str(stmt)
        if "pg_roles" in sql:
            result.scalar.return_value = 1
        elif "pg_tables" in sql or "tableowner" in sql:
            result.scalar.return_value = "sourcemind"
        return result

    bind.execute.side_effect = execute

    migration.upgrade()

    statements = [" ".join(c.args[0].split()) for c in migration.op.execute.call_args_list]
    assert not [s for s in statements if s.startswith("GRANT")]
    assert not [s for s in statements if 'FROM "sourcemind"' in s]
    assert "REVOKE ALL ON TABLE github_author_links FROM PUBLIC" in statements
