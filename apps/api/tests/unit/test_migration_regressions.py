"""Regression coverage for migration state restoration."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

API_ROOT = Path(__file__).resolve().parents[2]


def _load_security_foundation_migration():
    migration_path = API_ROOT / "alembic" / "versions" / "20260908_0006_security_foundation.py"
    spec = importlib.util.spec_from_file_location("security_foundation_0006", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _load_owner_removal_migration():
    migration_path = (
        API_ROOT / "alembic" / "versions" / "20260916_0009_serialize_owner_removal.py"
    )
    spec = importlib.util.spec_from_file_location("serialize_owner_removal_0009", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


@pytest.mark.unit
def test_0006_downgrade_restores_workspace_members_rls_flags() -> None:
    migration = _load_security_foundation_migration()
    migration.op = MagicMock()

    migration.downgrade()

    statements = [call.args[0] for call in migration.op.execute.call_args_list]
    assert "ALTER TABLE workspace_members DISABLE ROW LEVEL SECURITY" in statements
    assert "ALTER TABLE workspace_members NO FORCE ROW LEVEL SECURITY" in statements


@pytest.mark.unit
def test_0006_downgrade_rejects_connector_types_unsupported_by_0005() -> None:
    migration = _load_security_foundation_migration()
    migration.op = MagicMock()
    migration.op.get_bind.return_value.execute.side_effect = IntegrityError(
        "preflight", {}, Exception("check violation")
    )

    with pytest.raises(RuntimeError, match="connector types unsupported by 0005"):
        migration.downgrade()

    migration.op.execute.assert_not_called()
    migration.op.drop_constraint.assert_not_called()


@pytest.mark.unit
def test_0009_blocks_incompatible_rollback_before_changing_schema() -> None:
    migration = _load_owner_removal_migration()
    migration.op = MagicMock()
    migration.context = MagicMock()
    migration.context.get_revision_argument.return_value = "20250817_0005"
    migration.op.get_bind.return_value.execute.side_effect = IntegrityError(
        "preflight", {}, Exception("check violation")
    )

    with pytest.raises(RuntimeError, match="connector types unsupported by 0005"):
        migration.downgrade()

    migration.op.execute.assert_not_called()


@pytest.mark.unit
def test_0009_allows_downgrade_that_keeps_expanded_connector_schema() -> None:
    migration = _load_owner_removal_migration()
    migration.op = MagicMock()
    migration.context = MagicMock()
    migration.context.get_revision_argument.return_value = "20260916_0008"

    migration.downgrade()

    migration.op.get_bind.assert_not_called()
    assert migration.op.execute.call_count == 2
