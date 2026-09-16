"""
Shared pytest fixtures and configuration.

Real-DB tests require an explicit disposable local database URL plus an opt-in
flag. The legacy fixture names remain for compatibility, but no application
setting or shared database can be selected implicitly.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from collections.abc import AsyncIterator
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

os.environ["ENVIRONMENT"] = "development"
os.environ["DEBUG"] = "false"
os.environ["AUTH_DEV_BYPASS_ENABLED"] = "true"

_DISPOSABLE_HOSTS = {"127.0.0.1", "localhost"}
_DISPOSABLE_DATABASE_PORT = 55432
_DISPOSABLE_DATABASE_USER = "sourcemind_test"
_DISPOSABLE_DATABASE_NAME = "sourcemind_security_test"
_DISPOSABLE_REDIS_PORT = 56379
_DISPOSABLE_REDIS_DATABASE = "/15"


# ─── pg_ctl availability (legacy guard) ──────────────────────────────────────

def _pg_ctl_available() -> bool:
    """Return True if pg_ctl is available on PATH."""
    return shutil.which("pg_ctl") is not None


def _is_disposable_database_url(url: str) -> bool:
    try:
        parsed = make_url(url)
    except Exception:
        return False
    return (
        parsed.host in _DISPOSABLE_HOSTS
        and parsed.port == _DISPOSABLE_DATABASE_PORT
        and parsed.username == _DISPOSABLE_DATABASE_USER
        and parsed.database == _DISPOSABLE_DATABASE_NAME
    )


def _is_disposable_redis_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "redis"
            and parsed.hostname in _DISPOSABLE_HOSTS
            and parsed.port == _DISPOSABLE_REDIS_PORT
            and parsed.path == _DISPOSABLE_REDIS_DATABASE
        )
    except ValueError:
        return False


def _disposable_database_configured() -> bool:
    return (
        os.getenv("SECURITY_TEST_ALLOW_DISPOSABLE") == "1"
        and _is_disposable_database_url(os.getenv("TEST_DATABASE_URL", ""))
    )


pg_available = pytest.mark.skipif(
    not (_pg_ctl_available() or _disposable_database_configured()),
    reason="requires pg_ctl or the explicitly opted-in disposable PostgreSQL",
)


# ─── anyio backend (used by anyio-style tests elsewhere) ─────────────────────

@pytest.fixture
def anyio_backend():
    return "asyncio"


# ─── Disposable real-DB fixtures ─────────────────────────────────────────────

@pytest.fixture(scope="session")
def supabase_url() -> str:
    """Return an explicitly approved disposable local database URL."""
    if os.getenv("SECURITY_TEST_ALLOW_DISPOSABLE") != "1":
        pytest.skip("Set SECURITY_TEST_ALLOW_DISPOSABLE=1 to use the disposable test DB")
    url = os.getenv("TEST_DATABASE_URL", "")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    if not url.startswith("postgresql+asyncpg"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if not _is_disposable_database_url(url):
        raise RuntimeError(
            "Refusing non-disposable TEST_DATABASE_URL; expected "
            "sourcemind_test@127.0.0.1:55432/sourcemind_security_test"
        )
    return url


@pytest.fixture(scope="session")
def security_test_redis_url() -> str:
    """Return an explicitly approved disposable local Redis URL."""
    if os.getenv("SECURITY_TEST_ALLOW_DISPOSABLE") != "1":
        pytest.skip("Set SECURITY_TEST_ALLOW_DISPOSABLE=1 to use disposable Redis")
    url = os.getenv("TEST_REDIS_URL", "")
    if not url:
        pytest.skip("TEST_REDIS_URL is not configured")
    if not _is_disposable_redis_url(url):
        raise RuntimeError(
            "Refusing non-disposable TEST_REDIS_URL; expected "
            "redis://127.0.0.1:56379/15"
        )
    return url


@pytest_asyncio.fixture
async def supabase_engine(supabase_url):
    """
    Async SQLAlchemy engine pointing at disposable local Postgres.

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
async def test_workspace(db_session: AsyncSession, test_org, test_user):
    """Insert and return a real Workspace row attached to test_org."""
    from sourcemind.core.database import (
        set_rls_user_context,
        set_rls_workspace_context,
    )
    from sourcemind.models.workspace import Workspace, WorkspaceMember

    workspace_id = uuid.uuid4()
    await set_rls_user_context(db_session, test_user.id)
    await set_rls_workspace_context(db_session, workspace_id)
    ws = Workspace(
        id=workspace_id,
        organization_id=test_org.id,
        created_by_user_id=test_user.id,
        name="Test Workspace",
        slug=_slug("test-ws"),
    )
    db_session.add(ws)
    await db_session.flush()
    db_session.add(
        WorkspaceMember(
            workspace_id=ws.id,
            user_id=test_user.id,
            role="owner",
        )
    )
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
        email=f"test-{suffix}@example.com",
        display_name="Test User",
    )
    db_session.add(user)
    await db_session.flush()
    return user


def _hash(content: str) -> str:
    """SHA-256 of content — Memory.content_hash is NOT NULL."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
