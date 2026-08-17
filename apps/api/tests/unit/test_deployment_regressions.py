"""Regression tests for bugs found during the live deployment debugging session.

Each of these shipped to production and cost real debugging time. None had a
test. They share a failure signature worth naming: **the system looked
healthy while being broken.** The API returned 200 from /health while every
Celery task died; Alembic hung with no output at all; a JWKS fallback URL
answered 401 forever instead of failing loudly.

Covered here:
  Bug 2 — alembic/env.py must pass a finite connect_timeout
  Bug 4 — every third-party import must be a declared dependency
  Bug 6 — a missing CLERK_PUBLISHABLE_KEY must fail loudly, not fall back
  Bug 7 — every database engine must build its URL from the shared property

Bugs 1, 3 and 5 are Dockerfile/platform-config issues with no Python surface;
they are guarded by comments at the relevant lines and documented in
ARCHITECTURE.md section 13. Bug 1 additionally has scripts/check_wheel.py.
"""

from __future__ import annotations

import ast
import pathlib
import sys
import tomllib
from importlib.metadata import PackageNotFoundError, distribution, packages_distributions
from unittest.mock import MagicMock, patch

import pytest

API_ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE_ROOT = API_ROOT / "sourcemind"


# ─────────────────────────────────────────────────────────────────────────────
# BUG 2 — Alembic must never connect without a timeout
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_alembic_passes_a_finite_connect_timeout():
    """alembic/env.py must set connect_timeout on its psycopg2 connection.

    Without it psycopg2 inherits the OS default — roughly 127s of TCP SYN
    retries on Linux, or indefinite against a blackholed route. Alembic emits
    no output before a connection succeeds, so an unreachable database
    presents as total silence, and a container healthcheck kills the process
    long before the OS timeout fires. The failure leaves nothing to diagnose.
    """
    sys.path.insert(0, str(API_ROOT))
    spec_path = API_ROOT / "alembic" / "env.py"
    source = spec_path.read_text(encoding="utf-8")

    # env.py runs migrations at import time, so exercise run_migrations_online
    # in isolation rather than importing the module.
    tree = ast.parse(source)
    func = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "run_migrations_online"
    )

    fake_engine = MagicMock()
    namespace = {
        "create_engine": MagicMock(return_value=fake_engine),
        "pool": MagicMock(),
        "settings": MagicMock(
            database_url="postgresql+asyncpg://u:p@db.example.com:5432/x",
            sync_database_url="postgresql+psycopg2://u:p@db.example.com:5432/x",
            requires_ssl=True,
        ),
        "do_run_migrations": MagicMock(),
        "Any": object,
    }
    exec(compile(ast.Module(body=[func], type_ignores=[]), "<env>", "exec"), namespace)
    namespace["run_migrations_online"]()

    create_engine = namespace["create_engine"]
    assert create_engine.called, "run_migrations_online never built an engine"
    connect_args = create_engine.call_args.kwargs.get("connect_args")
    assert connect_args is not None, "create_engine called without connect_args"
    assert "connect_timeout" in connect_args, (
        "connect_timeout is absent — an unreachable database will hang "
        "silently past any healthcheck window"
    )
    timeout = connect_args["connect_timeout"]
    assert isinstance(timeout, int) and 0 < timeout <= 60, (
        f"connect_timeout must be a small finite value, got {timeout!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# BUG 4 — every third-party import must be declared
# ─────────────────────────────────────────────────────────────────────────────

def _declared_dependency_names() -> set[str]:
    data = tomllib.loads((API_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    specs = list(data["project"]["dependencies"])
    for extra in data["project"].get("optional-dependencies", {}).values():
        specs.extend(extra)

    names = set()
    for spec in specs:
        base = spec.split(";")[0].split("[")[0]
        for sep in (">=", "==", "<=", "~=", ">", "<", "!="):
            base = base.split(sep)[0]
        names.add(base.strip().lower().replace("_", "-"))
    return names


def _top_level_imports() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in PACKAGE_ROOT.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.setdefault(alias.name.split(".")[0], set()).add(path.name)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.setdefault(node.module.split(".")[0], set()).add(path.name)
    return found


# Modules that resolve through a differently-named distribution, or that are
# guaranteed transitively. Each needs a justification, not just an entry.
_IMPORT_ALIASES = {
    "Levenshtein": "python-levenshtein",   # python-Levenshtein requires Levenshtein
    "fitz": "pymupdf",                     # PyMuPDF installs as fitz
    "jwt": "pyjwt",                        # PyJWT installs as jwt
    "starlette": "fastapi",                # hard dependency of fastapi
    "jose": "python-jose",                 # python-jose installs as jose
    "celery": "celery",
    "redis": "redis",
}

# Declared with an environment marker, so absent on some interpreters.
_CONDITIONAL = {"tree_sitter_languages"}


@pytest.mark.unit
def test_every_third_party_import_is_declared():
    """No module under sourcemind/ may import an undeclared package.

    aiohttp reached production undeclared: slack_bolt.async_app imports it,
    slack-bolt declares only slack_sdk and publishes no extras, and it
    resolved locally only as a transitive dependency of kubernetes via
    chromadb. The app imported fine here and died in a clean image. This test
    catches that entire class of bug rather than aiohttp specifically.
    """
    declared = _declared_dependency_names()
    stdlib = set(sys.stdlib_module_names)
    mapping = packages_distributions()

    undeclared: list[str] = []
    for module, files in sorted(_top_level_imports().items()):
        if module in stdlib or module == "sourcemind" or module in _CONDITIONAL:
            continue
        if _IMPORT_ALIASES.get(module, "").lower() in declared:
            continue

        dists = mapping.get(module, [])
        if not dists:
            try:
                distribution(module)
                dists = [module]
            except PackageNotFoundError:
                undeclared.append(f"{module} (NOT INSTALLED) used in {sorted(files)}")
                continue
        if not any(d.lower().replace("_", "-") in declared for d in dists):
            undeclared.append(f"{module} (provided by {dists}) used in {sorted(files)}")

    assert not undeclared, (
        "Third-party imports with no declaration in pyproject.toml:\n  "
        + "\n  ".join(undeclared)
    )


# ─────────────────────────────────────────────────────────────────────────────
# BUG 6 — a missing publishable key must fail loudly
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_key",
    ["", "pk_test_", "garbage", "pk_test_!!!not-base64!!!"],
    ids=["empty", "no-payload", "malformed", "undecodable"],
)
def test_clerk_jwks_url_fails_loudly_on_bad_key(bad_key):
    """_clerk_jwks_url must raise rather than return an unusable fallback.

    It used to fall back to https://api.clerk.com/v1/jwks, an *authenticated*
    endpoint. The JWKS fetch sends no credentials, so it answers 401 forever
    and every token verification fails — surfacing only as a confusing 401 on
    every request, far from the real cause. A fallback that can never succeed
    is worse than no fallback.
    """
    from sourcemind.core.dependencies import _clerk_jwks_url
    from sourcemind.core.exceptions import InternalError

    with pytest.raises(InternalError) as exc_info:
        _clerk_jwks_url(bad_key)
    assert "CLERK_PUBLISHABLE_KEY" in str(exc_info.value), (
        "the error must name the variable to fix"
    )


@pytest.mark.unit
def test_clerk_jwks_url_never_returns_the_authenticated_fallback():
    """Belt and braces: the dead fallback must not exist as a string literal.

    Checked via AST rather than text search, so prose mentioning the URL in a
    docstring or comment — as this module and the function itself both do —
    does not trip it. Only a real string constant counts.
    """
    from sourcemind.core import dependencies

    source = pathlib.Path(dependencies.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    # Identify docstring nodes by identity. Comparing against
    # ast.get_docstring() does not work: it returns the cleaned/dedented text
    # while the Constant node holds the raw string.
    docstring_ids = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstring_ids.add(id(body[0].value))

    offending = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "api.clerk.com/v1/jwks" in node.value
        and id(node) not in docstring_ids
    ]
    assert not offending, (
        "api.clerk.com/v1/jwks requires authentication and always 401s as a "
        f"JWKS source; found as a string literal: {offending}"
    )


@pytest.mark.unit
def test_production_requires_clerk_publishable_key():
    """Production must refuse to boot without it, rather than 401 later."""
    from sourcemind.core.config import Settings

    with pytest.raises(ValueError, match="CLERK_PUBLISHABLE_KEY"):
        Settings(
            environment="production",
            openai_api_key="x",
            anthropic_api_key="x",
            clerk_secret_key="x",
            sentry_dsn="x",
            clerk_publishable_key="",
        )


# ─────────────────────────────────────────────────────────────────────────────
# BUG 7 — every engine must normalise the database URL
# ─────────────────────────────────────────────────────────────────────────────

BARE_URL = "postgresql://u:p@postgres.railway.internal:5432/railway"


@pytest.mark.unit
def test_settings_normalises_a_bare_postgres_url():
    """A driver-less URL must become asyncpg at the Settings boundary.

    Platform-provisioned Postgres injects `postgresql://...`. SQLAlchemy then
    defaults to psycopg2 and create_async_engine raises
    "The asyncio extension requires an async driver".
    """
    from sourcemind.core.config import Settings

    assert Settings(database_url=BARE_URL).database_url.startswith(
        "postgresql+asyncpg://"
    )


@pytest.mark.unit
def test_normalisation_is_idempotent():
    from sourcemind.core.config import Settings

    already = "postgresql+asyncpg://u:p@host:5432/db"
    assert Settings(database_url=already).database_url == already


@pytest.mark.unit
def test_derived_urls_strip_ssl_query_param():
    """asyncpg rejects ?ssl=require as a query param; it goes in connect_args."""
    from sourcemind.core.config import Settings

    s = Settings(database_url="postgresql://u:p@x.proxy.rlwy.net:5432/db?ssl=require")
    assert "ssl=require" not in s.async_database_url
    assert s.async_database_url.startswith("postgresql+asyncpg://")
    assert s.sync_database_url.startswith("postgresql+psycopg2://")
    assert s.requires_ssl is True


@pytest.mark.unit
def test_requires_ssl_is_false_only_on_the_private_network():
    """Railway's private network does not terminate TLS."""
    from sourcemind.core.config import Settings

    internal = Settings(database_url="postgresql://u:p@postgres.railway.internal:5432/db")
    public = Settings(database_url="postgresql://u:p@x.proxy.rlwy.net:5432/db")
    assert internal.requires_ssl is False
    assert public.requires_ssl is True


@pytest.mark.unit
def test_every_engine_entry_point_normalises_the_url():
    """All three async engine builders must produce an asyncpg URL.

    This is the regression that matters most. The normalisation originally
    lived in core/database.py only, so the API booted, reported healthy and
    served traffic while workers/ingestion.py and workers/connector_tasks.py
    built engines from the raw URL and died on every task — the
    "everything looks fine but nothing works" failure.
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    from sourcemind.core.config import Settings

    settings = Settings(database_url=BARE_URL)
    captured: list[str] = []

    def _capture(url, *args, **kwargs):
        captured.append(str(url))
        return MagicMock()

    # 1. core/database.py
    import sourcemind.core.database as database

    with patch.object(database, "create_async_engine", _capture):
        database._build_engine(settings)

    # 2 & 3. the two worker modules, which construct engines inline
    for module_path in (
        PACKAGE_ROOT / "workers" / "ingestion.py",
        PACKAGE_ROOT / "workers" / "connector_tasks.py",
    ):
        source = module_path.read_text(encoding="utf-8")
        assert "create_async_engine(\n        settings.database_url" not in source, (
            f"{module_path.name} builds an engine from the raw settings.database_url; "
            "it must use settings.async_database_url"
        )
        assert "settings.async_database_url" in source, (
            f"{module_path.name} does not use the shared async_database_url property"
        )
        captured.append(settings.async_database_url)

    assert captured, "no engine URL was captured"
    for url in captured:
        assert url.startswith("postgresql+asyncpg://"), (
            f"engine built with a non-async driver: {url}"
        )

    # Sanity: the shared property really does satisfy create_async_engine
    engine = create_async_engine(settings.async_database_url)
    assert engine.url.drivername == "postgresql+asyncpg"


@pytest.mark.unit
def test_no_module_derives_the_database_url_inline():
    """Only config.py may transform the database URL.

    Four modules each derived it themselves, with slightly different rules,
    and the two worker modules were missed when the bare-URL fix landed.
    Duplication is the root cause, so it is banned by test.
    """
    offenders: list[str] = []
    allowed = {"config.py"}

    for path in list(PACKAGE_ROOT.rglob("*.py")) + [API_ROOT / "alembic" / "env.py"]:
        if "__pycache__" in str(path) or path.name in allowed:
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if 'replace("postgresql' in stripped or "replace('postgresql" in stripped:
                offenders.append(f"{path.name}:{i}: {stripped[:80]}")

    assert not offenders, (
        "database URL rewriting must live only in core/config.py:\n  "
        + "\n  ".join(offenders)
    )
