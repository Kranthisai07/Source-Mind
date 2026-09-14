"""Validate the security-acceptance configuration before provisioning anything.

Run as the first CI step. Checks three things that are cheap here and expensive
to diagnose later, and prints no secret values in the process.

1. Every required credential is present AND non-empty.
   compose's `${VAR:?...}` already rejects both unset and empty (the colon form
   tests for null as well as unset), so this is not closing a hole in compose.
   It runs anyway because it fires BEFORE any container starts, names the
   offending variable directly, and covers the two URLs compose never sees —
   turning "container exited during init" into one sentence naming the cause.

2. The disposable URLs satisfy the guard conftest.py enforces.
   `supabase_url` and `security_test_redis_url` call `pytest.skip()` when the
   environment is wrong, so a misconfigured URL does not fail the run — it
   empties it. Eight skips and a green tick is the failure mode this exists to
   prevent.

3. The expected test node IDs still exist in the files.
   A rename would otherwise silently shrink the acceptance gate.

Exits non-zero with a specific message. No network, no database, no secrets in
output — only names, lengths and booleans.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[2]
CONFTEST = REPO / "apps" / "api" / "tests" / "conftest.py"

# Credentials the compose file demands. Names only; values are never printed.
REQUIRED_SECRETS = (
    "SECURITY_TEST_BOOTSTRAP_PASSWORD",
    "SECURITY_TEST_OWNER_PASSWORD",
    "SECURITY_TEST_RUNTIME_PASSWORD",
)

# URLs the test fixtures consume.
REQUIRED_URLS = ("TEST_DATABASE_URL", "TEST_REDIS_URL")

# The disposable tuple conftest.py pins. Mirrored so a change on either side is
# caught here rather than as a skip.
DISPOSABLE_HOSTS = {"127.0.0.1", "localhost"}
DISPOSABLE_DB_PORT = 55432
DISPOSABLE_DB_USER = "sourcemind_test"
DISPOSABLE_DB_NAME = "sourcemind_security_test"
DISPOSABLE_REDIS_PORT = 56379

# The exact acceptance gate. 6 + 2.
EXPECTED_NODES = {
    "apps/api/tests/integration/test_security_foundation_real_db.py": (
        "test_populated_memory_is_hidden_from_both_unauthorized_caller_types",
        "test_workspace_context_cannot_substitute_for_membership",
        "test_cross_workspace_insert_update_and_delete_are_blocked",
        "test_rls_context_is_transaction_local_on_reused_connection",
        "test_database_rejects_removing_the_last_active_owner",
        "test_all_workspace_policies_require_active_user_access",
    ),
    "apps/api/tests/integration/test_security_acceptance_real_services.py": (
        "test_signed_api_role_matrix_revocation_and_worker_reauthorization",
        "test_real_redis_counters_scopes_expiry_sharing_and_fail_closed",
    ),
}

_problems: list[str] = []


def problem(message: str) -> None:
    _problems.append(message)
    print(f"::error::{message}")


def check_secrets() -> None:
    for name in REQUIRED_SECRETS:
        value = os.environ.get(name)
        if value is None:
            problem(f"{name} is not set; compose requires it")
        elif not value.strip():
            problem(
                f"{name} is set but empty. compose would also reject this "
                "(`${VAR:?}` tests for null as well as unset); failing here "
                "names the variable instead of surfacing as a container that "
                "exited during initialization."
            )
        else:
            print(f"  {name}: present (length {len(value)})")


def check_database_url() -> None:
    raw = os.environ.get("TEST_DATABASE_URL", "")
    if not raw:
        problem("TEST_DATABASE_URL is not set; supabase_url would skip")
        return
    # Normalise the SQLAlchemy driver prefix so urlsplit can parse it.
    parsed = urlsplit(raw.replace("postgresql+asyncpg://", "postgresql://", 1))
    db = (parsed.path or "").lstrip("/")
    for label, actual, expected in (
        ("host", parsed.hostname, DISPOSABLE_HOSTS),
        ("port", parsed.port, {DISPOSABLE_DB_PORT}),
        ("username", parsed.username, {DISPOSABLE_DB_USER}),
        ("database", db, {DISPOSABLE_DB_NAME}),
    ):
        if actual not in expected:
            one = next(iter(expected)) if len(expected) == 1 else sorted(expected)
            problem(
                f"TEST_DATABASE_URL {label}={actual!r} is not the disposable "
                f"value {one!r}; conftest.py would refuse or skip it"
            )
    if not parsed.password:
        problem("TEST_DATABASE_URL carries no password")
    else:
        print(f"  TEST_DATABASE_URL: {parsed.username}@{parsed.hostname}:{parsed.port}/{db}")


def check_redis_url() -> None:
    raw = os.environ.get("TEST_REDIS_URL", "")
    if not raw:
        problem("TEST_REDIS_URL is not set; security_test_redis_url would skip")
        return
    parsed = urlsplit(raw)
    if parsed.hostname not in DISPOSABLE_HOSTS:
        problem(f"TEST_REDIS_URL host={parsed.hostname!r} is not local")
    if parsed.port != DISPOSABLE_REDIS_PORT:
        problem(
            f"TEST_REDIS_URL port={parsed.port!r} != {DISPOSABLE_REDIS_PORT}; "
            "conftest.py pins the disposable Redis port"
        )
    print(f"  TEST_REDIS_URL: {parsed.hostname}:{parsed.port}{parsed.path}")


def check_opt_in() -> None:
    if os.environ.get("SECURITY_TEST_ALLOW_DISPOSABLE") != "1":
        problem(
            "SECURITY_TEST_ALLOW_DISPOSABLE != '1'; every acceptance fixture "
            "would skip and the job would pass without testing anything"
        )


def check_nodes_exist() -> None:
    for rel, names in EXPECTED_NODES.items():
        path = REPO / rel
        if not path.exists():
            problem(f"missing test file {rel}")
            continue
        text = path.read_text(encoding="utf-8")
        found = set(re.findall(r"^(?:async )?def (test_\w+)", text, re.M))
        for name in names:
            if name not in found:
                problem(
                    f"{rel}::{name} no longer exists — the acceptance gate "
                    "would silently shrink"
                )
    total = sum(len(v) for v in EXPECTED_NODES.values())
    print(f"  expected node IDs: {total}")


def check_conftest_guard_intact() -> None:
    if not CONFTEST.exists():
        problem("apps/api/tests/conftest.py is missing")
        return
    text = CONFTEST.read_text(encoding="utf-8")
    for token in (str(DISPOSABLE_DB_PORT), DISPOSABLE_DB_USER,
                  DISPOSABLE_DB_NAME, str(DISPOSABLE_REDIS_PORT)):
        if token not in text:
            problem(
                f"conftest.py no longer references {token!r}; the disposable "
                "guard this script mirrors has changed. Update EXPECTED_* here."
            )


def main() -> int:
    print("security-acceptance contract preflight")
    check_opt_in()
    check_secrets()
    check_database_url()
    check_redis_url()
    check_nodes_exist()
    check_conftest_guard_intact()

    if _problems:
        print(f"\n{len(_problems)} problem(s); refusing to provision.")
        return 1
    print("\ncontract ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
