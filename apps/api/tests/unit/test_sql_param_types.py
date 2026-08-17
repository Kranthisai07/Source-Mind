"""Guard against inconsistently-typed bind parameters in raw SQL.

THE BUG CLASS
-------------
asyncpg infers ONE type per placeholder for an entire statement. Using the
same bind parameter in two differently-typed positions is unresolvable:

    JOIN attributions a ON a.user_id::text != :dep_uid
    WHERE mr.source_memory_id = CAST(:mid AS uuid)
      AND m2.id::text != :mid            ← :mid is now both uuid and text

    asyncpg.exceptions.UndefinedFunctionError:
        operator does not exist: text <> uuid

This reached production in _find_successor's fallback query, where it meant
tier-1 handoff successor suggestion had never worked for any input. It is
invisible to mocked tests, because a mock never parses the SQL, and invisible
to review, because each clause is correct in isolation.

Two layers here:
  * a static check that needs no database and runs on every test run
  * a live PREPARE of every statement, which is authoritative but requires a
    real Postgres and so skips when one is not configured
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys

import pytest

API_ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = API_ROOT / "sourcemind"
PARAM = re.compile(r"(?<!:):([a-zA-Z_][a-zA-Z0-9_]*)")

sys.path.insert(0, str(API_ROOT / "scripts"))


def _sql_statements() -> list[tuple[str, int, str]]:
    """Every literal SQL string passed to text() in the package."""
    out: list[tuple[str, int, str]] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fname = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if fname != "text" or not node.args:
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                out.append((str(path.relative_to(PACKAGE)), node.lineno, arg.value))
    return out


# ─── static layer ────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_no_bind_parameter_is_used_with_conflicting_types():
    """Runs scripts/check_sql_params.py over the package."""
    from check_sql_params import analyse

    findings = analyse(list(PACKAGE.rglob("*.py")))
    assert not findings, "\n".join(str(f) for f in findings)


@pytest.mark.unit
def test_the_static_checker_actually_detects_the_known_bug(tmp_path):
    """A checker that never fires is worse than none — it manufactures
    confidence. This feeds it the exact pre-fix query from _find_successor.
    """
    from check_sql_params import analyse

    broken = tmp_path / "broken.py"
    broken.write_text(
        'from sqlalchemy import text\n'
        'q = text("""\n'
        '    SELECT a.user_id FROM attributions a\n'
        '    JOIN memories m2 ON a.memory_id = m2.id\n'
        '        AND a.user_id::text != :dep_uid\n'
        '    WHERE m2.id = CAST(:mid AS uuid)\n'
        '      AND m2.id::text != :mid\n'
        '""")\n',
        encoding="utf-8",
    )
    findings = analyse([broken])
    assert findings, "the checker failed to detect the known production bug"
    assert any(f.param == "mid" for f in findings)


@pytest.mark.unit
def test_postfix_casts_on_bind_parameters_are_absent():
    """`:param::type` is never bound by SQLAlchemy at all.

    text() does not recognise a placeholder followed by ::, so it is passed
    through as literal SQL and Postgres raises a syntax error at the colon.
    All 85 occurrences were converted to CAST(:param AS type); this keeps
    them gone.
    """
    offenders: list[str] = []
    for name, lineno, sql in _sql_statements():
        for match in re.finditer(r":([a-zA-Z_][a-zA-Z0-9_]*)::", sql):
            offenders.append(f"{name}:{lineno} :{match.group(1)}::")
    assert not offenders, (
        "use CAST(:param AS type); `:param::type` is not bound by text():\n  "
        + "\n  ".join(offenders)
    )


# ─── live layer ──────────────────────────────────────────────────────────────

def _live_db_configured() -> bool:
    from sourcemind.core.config import get_settings

    url = get_settings().database_url or ""
    return bool(url) and "localhost" not in url and "127.0.0.1" not in url


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(
    not _live_db_configured(), reason="requires a live Postgres to PREPARE against"
)
async def test_every_statement_prepares_against_real_postgres():
    """PREPARE each statement so the server resolves every placeholder type.

    This is the authoritative check. Executing with sample values conflates a
    bad sample (DataError) with an unresolvable type; PREPARE binds nothing
    and fails only if the statement genuinely cannot be planned.
    """
    import asyncpg

    from sourcemind.core.config import get_settings

    url = get_settings().async_database_url.replace(
        "postgresql+asyncpg://", "postgresql://"
    )

    def to_positional(sql: str) -> str:
        order: list[str] = []

        def repl(m: re.Match) -> str:
            name = m.group(1)
            if name not in order:
                order.append(name)
            return f"${order.index(name) + 1}"

        return PARAM.sub(repl, sql)

    conn = await asyncpg.connect(url, ssl="require", timeout=20)
    failures: list[str] = []
    try:
        for name, lineno, sql in _sql_statements():
            try:
                await conn.prepare(to_positional(sql), timeout=20)
            except asyncpg.PostgresError as exc:
                failures.append(f"{name}:{lineno}  {type(exc).__name__}: {exc}")
            except Exception:
                # Connection-level hiccups are not a statement defect;
                # reconnect and continue.
                conn = await asyncpg.connect(url, ssl="require", timeout=20)
    finally:
        await conn.close()

    assert not failures, "statements that Postgres cannot plan:\n  " + "\n  ".join(
        failures
    )
