"""Assert the web integration suite actually ran, rather than merely exiting 0.

Jest exits 0 when every test is skipped. The integration suite skips itself
whenever `E2E_API_URL` is unset — which is correct for a developer's machine and
catastrophic for a CI gate, because a job that loses its database configuration
would report success while asserting nothing. Locally, with no stack running,
the suite reports sixteen tests with status "pending" and exit code 0. That is
precisely what a broken CI run would look like.

So the exit status is not the gate. This is:

  1. Every expected test is present in the report.
  2. No unexpected test is present (a rename shows up as one of each).
  3. Every one of them has status "passed" — "pending" is a failure here.

Reads jest's `--json` output. Prints names and counts only; it never sees a
credential.

Usage: check_web_integration_report.py <report.json> [expected.txt]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_EXPECTED = REPO / ".github" / "scripts" / "expected_web_integration_tests.txt"

# Jest statuses that mean "this test did not run". Anything here is a failure
# for a gate, however green the exit code.
NOT_EXECUTED = {"pending", "skipped", "todo", "disabled"}


def load_expected(path: Path) -> list[str]:
    if not path.exists():
        sys.exit(f"::error::expected-test manifest missing: {path}")
    names = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not names:
        sys.exit(f"::error::expected-test manifest {path} lists no tests")
    return names


def load_report(path: Path) -> list[dict]:
    if not path.exists():
        sys.exit(f"::error::no jest report at {path} — the test step did not run")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        sys.exit(f"::error::jest report at {path} is not valid JSON: {exc}")

    results = []
    for suite in data.get("testResults", []):
        # A suite that failed to load has no assertions at all; its message is
        # the only clue, and losing it turns a real error into "0 tests found".
        if suite.get("status") == "failed" and not suite.get("assertionResults"):
            message = (suite.get("message") or "").strip().splitlines()
            head = message[0] if message else "no message"
            print(f"::error::suite failed to run: {suite.get('name')}: {head}")
        results.extend(suite.get("assertionResults", []))
    return results


def main() -> int:
    if len(sys.argv) < 2:
        sys.exit("usage: check_web_integration_report.py <report.json> [expected.txt]")

    report_path = Path(sys.argv[1])
    expected_path = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_EXPECTED

    expected = load_expected(expected_path)
    assertions = load_report(report_path)

    by_name: dict[str, str] = {}
    for a in assertions:
        name = a.get("fullName") or a.get("title") or "<unnamed>"
        by_name[name] = a.get("status", "unknown")

    expected_set = set(expected)
    actual_set = set(by_name)

    missing = sorted(expected_set - actual_set)
    unexpected = sorted(actual_set - expected_set)
    not_executed = sorted(
        n for n in expected_set & actual_set if by_name[n] in NOT_EXECUTED
    )
    failed = sorted(
        n for n in expected_set & actual_set
        if by_name[n] not in NOT_EXECUTED and by_name[n] != "passed"
    )

    print(f"expected {len(expected)} test(s); report contains {len(by_name)}")

    for name in missing:
        print(f"::error::expected test is absent from the report: {name}")
    for name in unexpected:
        print(f"::error::unexpected test in the report (rename?): {name} -> {by_name[name]}")
    for name in not_executed:
        print(
            f"::error::required test did not execute ({by_name[name]}): {name}"
            " — the suite skips itself when E2E_API_URL is unset"
        )
    for name in failed:
        print(f"::error::required test did not pass ({by_name[name]}): {name}")

    ok = not (missing or unexpected or not_executed or failed)
    print("all expected integration tests executed and passed" if ok
          else "integration gate FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
