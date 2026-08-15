"""
Shared pytest fixtures and configuration.

Two real-DB strategies are supported:

  1. **Supabase-backed integration tests** (the active path).
     Fixtures: `supabase_url`, `supabase_engine`, `db_session`,
     plus convenience factories `test_org`, `test_workspace`, `test_user`.
     Each `db_session` runs inside an outer transaction with
     `join_transaction_mode="create_savepoint"`, so any commit inside the
     code-under-test releases a SAVEPOINT and the outer transaction is
     rolled back at teardown — no test data leaks into Supabase.

  2. **Local pytest-postgresql** (legacy, kept as a fallback guard).
     The `pg_available` mark skips tests when `pg_ctl` is missing from PATH.
"""

from __future__ import annotations

import hashlib
import shutil
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine


# ─── pg_ctl availability (legacy guard) ──────────────────────────────────────

def _pg_ctl_available() -> bool:
    """Return True if pg_ctl is available on PATH."""
    return shutil.which("pg_ctl") is not None


pg_available = pytest.mark.skipif(
    not _pg_ctl_available(),
    reason="pg_ctl not found on PATH. Install PostgreSQL or add it to PATH to run real-DB tests.",
)


# ─── anyio backend (used by anyio-style tests elsewhere) ─────────────────────

@pytest.fixture
def anyio_backend():
    return "asyncio"


# ─── Supabase real-DB fixtures ───────────────────────────────────────────────

@pytest.fixture(scope="session")
def supabase_url() -> str:
    """
    Return the async DATABASE_URL from app settings.

    Skips the entire test if DATABASE_URL is unset or points at localhost
    (real-DB tests are designed against the cloud Supabase instance, not
    a local Postgres — local Postgres lacks pgvector here).
    """
    from sourcemind.core.config import get_settings
    settings = get_settings()
    url = settings.database_url
    if not url or "localhost" in url or "127.0.0.1" in url:
        pytest.skip("Supabase DATABASE_URL not configured (got localhost or empty)")
    if not url.startswith("postgresql+asyncpg"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


@pytest_asyncio.fixture
async def supabase_engine(supabase_url):
    """
    Async SQLAlchemy engine pointing at Supabase.

    Function-scoped so each test gets a fresh asyncpg pool bound to the
    current test's event loop. Avoids "Event loop is closed" errors that
    occur when a session-scoped engine outlives the per-test loop on
    Windows + Python 3.14 + pytest-asyncio.
    """
    engine = create_async_engine(
        supabase_url,
        echo=False,
        pool_size=2,
        max_overflow=0,
        pool_pre_ping=True,
    )
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def db_session(supabase_engine) -> AsyncIterator[AsyncSession]:
    """
    Per-test async session with full transaction rollback at teardown.

    Pattern (SQLAlchemy 2.0):
      - Open a connection and begin an outer transaction.
      - Bind an AsyncSession with `join_transaction_mode="create_savepoint"`.
        Any `session.commit()` inside the test now releases a SAVEPOINT
        rather than committing the outer transaction.
      - At teardown, roll back the outer transaction — every INSERT/UPDATE
        the test performed disappears.
    """
    async with supabase_engine.connect() as conn:
        outer_trans = await conn.begin()
        async with AsyncSession(
            bind=conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        ) as session:
            try:
                yield session
            finally:
                await session.close()
        await outer_trans.rollback()


# ─── Test data factories ─────────────────────────────────────────────────────

def _slug(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@pytest_asyncio.fixture
async def test_org(db_session: AsyncSession):
    """Insert and return a real Organization row (random slug avoids collisions)."""
    from sourcemind.models.organization import Organization
    org = Organization(
        name="Test Org",
        slug=_slug("test-org"),
        plan="free",
    )
    db_session.add(org)
    await db_session.flush()
    return org


@pytest_asyncio.fixture
async def test_workspace(db_session: AsyncSession, test_org):
    """Insert and return a real Workspace row attached to test_org."""
    from sourcemind.models.workspace import Workspace
    ws = Workspace(
        organization_id=test_org.id,
        name="Test Workspace",
        slug=_slug("test-ws"),
    )
    db_session.add(ws)
    await db_session.flush()
    return ws


@pytest_asyncio.fixture
async def test_user(db_session: AsyncSession):
    """
    Insert and return a real User row.

    The User model requires a unique clerk_id and email. We use random
    suffixes so concurrent test runs (or repeated runs sharing state)
    don't collide on the unique constraints.
    """
    from sourcemind.models.user import User
    suffix = uuid.uuid4().hex[:12]
    user = User(
        clerk_id=f"clerk-test-{suffix}",
        email=f"test-{suffix}@test.local",
        display_name="Test User",
    )
    db_session.add(user)
    await db_session.flush()
    return user


def _hash(content: str) -> str:
    """SHA-256 of content — Memory.content_hash is NOT NULL."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
