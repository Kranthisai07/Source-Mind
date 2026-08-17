"""Static check for inconsistently-typed bind parameters in raw SQL.

THE BUG CLASS
-------------
asyncpg infers ONE type per placeholder across an entire statement. A query
that uses the same bind parameter in two differently-typed positions cannot
be resolved and fails at execution:

    JOIN attributions a ON a.user_id::text != :dep_uid   -- text
    ...
    WHERE mr.source_memory_id = CAST(:mid AS uuid)       -- uuid
      AND m2.id::text != :mid                            -- text  ← conflict

    asyncpg.exceptions.UndefinedFunctionError:
        operator does not exist: text <> uuid

This is invisible to unit tests that mock the session — the SQL is never
parsed — and invisible to review, because each clause looks correct on its
own. It reached production once in _find_successor, where it meant tier-1
handoff successor suggestion had never worked for any input.

WHAT THIS FLAGS
---------------
For every `text(...)` SQL string, each bind parameter that appears more than
once is checked for a consistent casting context. A parameter used as both
CAST(:p AS uuid) and in a bare comparison against a ::text expression is
reported.

Usage:
    python scripts/check_sql_params.py           # repo-wide, exit 1 on findings
    python scripts/check_sql_params.py --verbose # list every query inspected
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import re
import sys

PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "sourcemind"

# A bind parameter, excluding PostgreSQL's :: cast operator.
_PARAM = re.compile(r"(?<!:):([a-zA-Z_][a-zA-Z0-9_]*)")
# CAST(:name AS type)
_CAST = re.compile(r"CAST\(\s*:([a-zA-Z_][a-zA-Z0-9_]*)\s+AS\s+(\w+)\s*\)", re.I)
# :name::type  (the postfix form; SQLAlchemy does not even bind these)
_POSTFIX = re.compile(r":([a-zA-Z_][a-zA-Z0-9_]*)::(\w+)")


class Finding:
    def __init__(self, file: str, line: int, param: str, contexts: set[str], sql: str):
        self.file = file
        self.line = line
        self.param = param
        self.contexts = contexts
        self.sql = sql

    def __str__(self) -> str:
        snippet = " ".join(self.sql.split())[:110]
        return (
            f"{self.file}:{self.line}  parameter :{self.param} "
            f"used as {sorted(self.contexts)}\n      {snippet}"
        )


def _sql_strings(tree: ast.AST) -> list[tuple[int, str]]:
    """Return (lineno, sql) for every text(...) call with a literal argument."""
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name != "text" or not node.args:
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            out.append((node.lineno, arg.value))
        elif isinstance(arg, ast.JoinedStr):
            # f-string: join the literal parts; interpolations are opaque here
            parts = [
                v.value for v in arg.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)
            ]
            out.append((node.lineno, "".join(parts)))
        elif isinstance(arg, ast.BinOp):
            try:
                out.append((node.lineno, ast.literal_eval(arg)))
            except Exception:
                pass
    return out


def _contexts_for(sql: str, param: str) -> set[str]:
    """Classify how each occurrence of `param` is typed."""
    contexts: set[str] = set()

    for match in _CAST.finditer(sql):
        if match.group(1) == param:
            contexts.add(f"CAST->{match.group(2).lower()}")
    for match in _POSTFIX.finditer(sql):
        if match.group(1) == param:
            contexts.add(f"postfix->{match.group(2).lower()}")

    # Bare occurrences: neither inside CAST(...) nor followed by ::
    covered: list[tuple[int, int]] = [
        m.span() for m in _CAST.finditer(sql) if m.group(1) == param
    ] + [m.span() for m in _POSTFIX.finditer(sql) if m.group(1) == param]

    for match in _PARAM.finditer(sql):
        if match.group(1) != param:
            continue
        if any(start <= match.start() < end for start, end in covered):
            continue
        # Look at what it is compared against; a ::text on the other side is
        # what actually forces the conflicting inference.
        before = sql[max(0, match.start() - 60) : match.start()]
        if "::text" in before.lower():
            contexts.add("bare(vs ::text)")
        elif "::uuid" in before.lower():
            contexts.add("bare(vs ::uuid)")
        else:
            contexts.add("bare")
    return contexts


def analyse(paths: list[pathlib.Path], verbose: bool = False) -> list[Finding]:
    findings: list[Finding] = []
    total_queries = 0
    repeated = 0

    for path in sorted(paths):
        if "__pycache__" in str(path):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue

        for lineno, sql in _sql_strings(tree):
            total_queries += 1
            names = _PARAM.findall(sql)
            multi = {n for n in names if names.count(n) > 1}
            if multi:
                repeated += 1
            if verbose:
                print(f"  {path.name}:{lineno}  params={sorted(set(names))}")
            for param in sorted(multi):
                contexts = _contexts_for(sql, param)
                # More than one distinct typing context is the bug.
                if len(contexts) > 1:
                    try:
                        display = str(path.relative_to(PACKAGE.parent))
                    except ValueError:
                        # Path outside the package (e.g. an ad-hoc file passed
                        # in to verify the checker itself).
                        display = str(path)
                    findings.append(Finding(display, lineno, param, contexts, sql))

    print(f"queries inspected            : {total_queries}")
    print(f"queries with a repeated param: {repeated}")
    print(f"inconsistently typed params  : {len(findings)}")
    return findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    findings = analyse(list(PACKAGE.rglob("*.py")), verbose=args.verbose)
    if findings:
        print()
        print("FINDINGS — asyncpg infers one type per placeholder:")
        for f in findings:
            print(f"  {f}")
        print()
        print("Fix: cast every occurrence of the parameter the same way, e.g.")
        print("  CAST(:id AS uuid) everywhere, never mixed with col::text = :id")
        return 1
    print("OK: no parameter is used with conflicting types")
    return 0


if __name__ == "__main__":
    sys.exit(main())
