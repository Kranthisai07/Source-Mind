"""
Shared pytest fixtures and configuration.

Real-database fixtures use pytest-postgresql, which requires pg_ctl to be on PATH.
All fixtures guarded by `pg_available` skip gracefully when pg_ctl is absent.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest


# ─── pg_ctl availability ──────────────────────────────────────────────────────

def _pg_ctl_available() -> bool:
    """Return True if pg_ctl is available on PATH."""
    return shutil.which("pg_ctl") is not None


pg_available = pytest.mark.skipif(
    not _pg_ctl_available(),
    reason="pg_ctl not found on PATH. Install PostgreSQL or add it to PATH to run real-DB tests.",
)


# ─── Real-DB fixture (pytest-postgresql) ─────────────────────────────────────

if _pg_ctl_available():
    try:
        from pytest_postgresql import factories as pg_factories

        # Process fixture: one PostgreSQL server per test session.
        # postgres_options avoids the pytest-postgresql default that wraps
        # log_destination in extra single-quotes on Windows (causes FATAL).
        _postgresql_proc = pg_factories.postgresql_proc(
            port=None,
            postgres_options="-F -c logging_collector=off",
        )
        # Client fixture: function-scoped (matches postgresql factory default)
        _postgresql = pg_factories.postgresql("_postgresql_proc")

        @pytest.fixture
        def pg_connection(_postgresql):
            """Yield a psycopg3 connection to the test PostgreSQL instance."""
            return _postgresql

    except ImportError:
        pass  # pytest-postgresql not installed — fixtures not defined


# ─── SQLAlchemy async session fixture ────────────────────────────────────────

@pytest.fixture
def anyio_backend():
    return "asyncio"
