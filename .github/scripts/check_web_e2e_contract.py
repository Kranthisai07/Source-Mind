"""Validate the web integration configuration before provisioning anything.

Runs as the first step of the integration job. Everything it checks is cheap
here and expensive to diagnose after three minutes of provisioning, and none of
it needs a database.

  1. Every required credential is present AND non-empty. Names and lengths
     only; values are never printed.
  2. Every required environment variable the harness reads is set.
  3. The support files the job feeds to psql and python actually exist.
  4. Every test named in the expected manifest still exists in the suite
     source. A rename would otherwise shrink the gate silently — the post-run
     check would catch it, but only after the whole stack had been built.

The disposable-database rules are deliberately NOT re-implemented here. They
live in apps/web/e2e/psqlClient.js, which is what actually enforces them at
runtime, and the workflow exercises that same code directly in a separate step.
A second copy in Python would drift from the first.

Exits non-zero with a message naming what is wrong.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WEB = REPO / "apps" / "web"
E2E = WEB / "e2e"
SUITE = WEB / "src" / "pages" / "conflictResolution.e2e.test.js"
MANIFEST = REPO / ".github" / "scripts" / "expected_web_integration_tests.txt"

# Injected from the secret store. Names only.
REQUIRED_SECRETS = (
    "E2E_DB_BOOTSTRAP_PASSWORD",
    "E2E_DB_OWNER_PASSWORD",
    "E2E_DB_RUNTIME_PASSWORD",
)

# Read by the suite and by psqlClient.js.
REQUIRED_ENV = (
    "E2E_API_URL",
    "E2E_ALLOW_DISPOSABLE",
    "E2E_PG_HOST",
    "E2E_PG_PORT",
    "E2E_PG_USER",
    "E2E_PG_DATABASE",
    "E2E_PSQL_MODE",
    "REACT_APP_USE_MOCKS",
    "REACT_APP_BACKEND_URL",
)

REQUIRED_FILES = (
    E2E / "init_roles.sql",
    E2E / "seed_base.sql",
    E2E / "seed_conflicts.sql",
    E2E / "openai_stub.py",
    E2E / "psqlClient.js",
    SUITE,
    MANIFEST,
)

# A double- or single-quoted test title, tolerating escaped quotes inside it.
TITLE_PATTERNS = (
    re.compile(r'test(?:\.each\([^)]*\))?\(\s*"((?:[^"\\]|\\.)*)"'),
    re.compile(r"test(?:\.each\([^)]*\))?\(\s*'((?:[^'\\]|\\.)*)'"),
)

failures: list[str] = []


def check_secrets() -> None:
    for name in REQUIRED_SECRETS:
        value = os.environ.get(name)
        if value is None:
            failures.append(f"secret {name} is not set")
        elif not value.strip():
            failures.append(f"secret {name} is set but empty")
        else:
            print(f"  {name}: present ({len(value)} chars)")


def check_env() -> None:
    for name in REQUIRED_ENV:
        value = os.environ.get(name)
        if not value:
            failures.append(f"environment variable {name} is not set")
        else:
            print(f"  {name}={value}")

    # The suite skips itself without this, and a skipped gate passing is the
    # exact failure mode the whole job exists to prevent.
    if not os.environ.get("E2E_API_URL"):
        failures.append(
            "E2E_API_URL is unset — the suite would skip and the job would pass"
        )

    if os.environ.get("REACT_APP_USE_MOCKS", "").lower() != "false":
        failures.append(
            "REACT_APP_USE_MOCKS must be exactly 'false' — any other value "
            "routes the suite at the mock client, so it would never reach the API"
        )


def check_files() -> None:
    for path in REQUIRED_FILES:
        if path.exists():
            print(f"  {path.relative_to(REPO)}: present")
        else:
            failures.append(f"required file missing: {path.relative_to(REPO)}")


def check_manifest_names() -> None:
    if not SUITE.exists() or not MANIFEST.exists():
        return  # already reported by check_files

    source = SUITE.read_text(encoding="utf-8")
    names = [
        line.strip()
        for line in MANIFEST.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not names:
        failures.append("expected-test manifest lists no tests")
        return

    # The manifest stores the full jest name ("<describe path> <title>"), and
    # only the title appears verbatim in the source — so an entry is satisfied
    # when it ends with a title the suite actually declares.
    declared: list[str] = []
    for pattern in TITLE_PATTERNS:
        declared.extend(pattern.findall(source))
    if not declared:
        failures.append(
            f"no test titles found in {SUITE.relative_to(REPO)} — title parser broken?"
        )
        return

    missing = [n for n in names if not any(n.endswith(t) for t in declared)]
    for name in missing:
        failures.append(f"expected test not found in suite source: {name}")
    print(
        f"  manifest: {len(names)} expected, "
        f"{len(names) - len(missing)} located among {len(declared)} declared titles"
    )


def main() -> int:
    print("secrets:")
    check_secrets()
    print("environment:")
    check_env()
    print("files:")
    check_files()
    print("expected tests:")
    check_manifest_names()

    if failures:
        print()
        for problem in failures:
            print(f"::error::{problem}")
        print(f"\npreflight FAILED ({len(failures)} problem(s))")
        return 1

    print("\npreflight ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
