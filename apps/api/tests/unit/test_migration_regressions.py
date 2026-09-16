"""Regression coverage for migration state restoration."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest

API_ROOT = Path(__file__).resolve().parents[2]


def _load_security_foundation_migration():
    migration_path = API_ROOT / "alembic" / "versions" / "20260908_0006_security_foundation.py"
    spec = importlib.util.spec_from_file_location("security_foundation_0006", migration_path)
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
