"""Assert the PostgreSQL 18 migration round trip actually held.

This is the gate, and it is deliberately the ONLY thing that decides the job.
No earlier step's exit code is treated as evidence on its own: the job runs
with `if: always()` here so that a phase which was skipped — because something
upstream failed, or because a step was removed — fails loudly instead of
leaving a green tick on a job that proved nothing. That failure mode is not
hypothetical; the web integration gate caught exactly it on an earlier
revision, where jest exited 0 having run nothing.

Four assertions:

  1. Every phase produced its evidence file. A missing file means that phase
     did not run, which is a failure, not an absence of information.
  2. The RLS flags after downgrade equal the flags MEASURED at the baseline
     revision — exactly, table for table. Not a hand-written expectation of
     what the baseline should be; the recording of what it actually was.
  3. Rows survived the round trip. Counts and a content digest are compared
     across seed → downgrade → re-upgrade.
  4. The restricted role sees what it should after re-upgrade: a member sees
     the rows, a non-member sees none, and no context sees none.

Prints only names, counts and booleans. It never sees a credential.

Usage: check_migration_roundtrip.py <evidence-dir>
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# Columns the 0007 downgrade drops by design, so their data is expected to be
# absent afterwards. Row COUNTS must still be preserved; that is what is
# checked. Listed here so the distinction is explicit rather than assumed.
DROPPED_BY_DESIGN = ("workspaces.created_by_user_id", "workspace_members.status")

# Per-phase expectations for the ownerless regression.
#
# The critical distinction, and why this is a table and not a boolean: at the
# previous 0007 the node is SUPPOSED to fail, because that failure IS the
# compatibility defect. But a node that could not run (`error`) or was never
# executed (`skipped`) also produces "not passed", and neither is evidence of
# anything.  Only an executed assertion failure counts as a reproduction.
#
# The same reasoning guards the rollback. A downgrade that SUCCEEDS and then
# reproduces the defect is a passing rollback. A downgrade whose alembic
# command failed is a failing one — and that is caught by the recorded
# revision, not by the test result, because a crashed migration leaves the test
# failing for reasons that have nothing to do with the defect.
REGRESSION_PHASES = {
    "prev": ("failure", "the defect must reproduce at the previous 0007"),
    "head": ("passed", "0008 must fix it at head"),
    "reupgrade": ("passed", "and it must survive the full round trip"),
}

EXPECTED_REVISIONS = {
    "prev": "20260909_0007",
    "head": "20260916_0008",
    "reupgrade": "20260916_0008",
}

REQUIRED = {
    "pgversion.txt": "PostgreSQL version record",
    "revision_prev.txt": "revision after upgrading to the previous 0007",
    "revision_head.txt": "revision at head",
    "revision_reupgrade.txt": "revision after the round trip",
    "regression_prev.xml": "regression result at the previous 0007",
    "regression_head.xml": "regression result at head",
    "regression_reupgrade.xml": "regression result after the round trip",
    "roles.txt": "role restriction check",
    "flags_baseline.txt": "RLS flags measured at the baseline revision",
    "counts_seeded.txt": "row counts after seeding",
    "flags_after_downgrade.txt": "RLS flags after downgrade",
    "counts_after_downgrade.txt": "row counts after downgrade",
    "flags_after_reupgrade.txt": "RLS flags after re-upgrade",
    "counts_after_reupgrade.txt": "row counts after re-upgrade",
    "restricted.txt": "restricted-role visibility",
}

failures: list[str] = []


def read_lines(path: Path) -> list[str]:
    return [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def parse_kv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in read_lines(path):
        if "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def main() -> int:
    if len(sys.argv) < 2:
        sys.exit("usage: check_migration_roundtrip.py <evidence-dir>")
    ev = Path(sys.argv[1])

    # ── 1. every phase ran ────────────────────────────────────────────────
    print("phases:")
    missing = False
    for name, what in REQUIRED.items():
        p = ev / name
        if p.exists() and p.read_text(encoding="utf-8").strip():
            print(f"  {name}: present ({what})")
        else:
            failures.append(f"missing or empty evidence: {name} — the {what} phase did not run")
            missing = True
    if missing:
        # Comparing absent files would produce confusing secondary errors.
        for f in failures:
            print(f"::error::{f}")
        print(f"\nround-trip gate FAILED ({len(failures)} problem(s))")
        return 1

    version = parse_kv(ev / "pgversion.txt")
    print(f"\npostgres: {version.get('server_version', '?')}")
    if not version.get("server_version", "").startswith("18."):
        failures.append(
            f"expected PostgreSQL 18, evidence records {version.get('server_version')!r}"
        )

    if "ok" not in (ev / "roles.txt").read_text(encoding="utf-8"):
        failures.append(
            "role check did not report ok — if the roles can bypass RLS, "
            "every visibility assertion below is vacuous"
        )

    # ── 2. flags returned to the measured baseline ────────────────────────
    baseline = read_lines(ev / "flags_baseline.txt")
    after = read_lines(ev / "flags_after_downgrade.txt")
    print("\nRLS flags, baseline vs after downgrade:")
    if baseline == after:
        print(f"  identical across all {len(baseline)} tables")
    else:
        b = dict(l.split("|", 1) for l in baseline)
        a = dict(l.split("|", 1) for l in after)
        for table in sorted(set(b) | set(a)):
            bv, av = b.get(table, "<absent>"), a.get(table, "<absent>")
            mark = "  " if bv == av else "  <-- DIFFERS"
            print(f"  {table:<22} baseline={bv:<14} after={av:<14}{mark}")
            if bv != av:
                failures.append(
                    f"{table}: downgrade left relrowsecurity|relforcerowsecurity "
                    f"at {av}, baseline was {bv}"
                )

    # ── 3. data survived ──────────────────────────────────────────────────
    seeded = parse_kv(ev / "counts_seeded.txt")
    down = parse_kv(ev / "counts_after_downgrade.txt")
    up = parse_kv(ev / "counts_after_reupgrade.txt")

    print("\nrow counts across the round trip:")
    for key in sorted(seeded):
        s, d, u = seeded[key], down.get(key, "<missing>"), up.get(key, "<missing>")
        ok = s == d == u
        print(f"  {key:<24} seeded={s:<36} downgrade={d:<36} reupgrade={u:<36}"
              f"{'' if ok else '  <-- CHANGED'}")
        if not ok:
            failures.append(
                f"{key} changed across the round trip: seeded={s}, "
                f"after downgrade={d}, after re-upgrade={u}"
            )

    if seeded.get("memories") in (None, "0"):
        failures.append(
            "no memories were seeded — a round trip over an empty database "
            "proves nothing about data preservation"
        )

    # ── 4. the restricted role ────────────────────────────────────────────
    r = parse_kv(ev / "restricted.txt")
    member, outsider, nocontext = r.get("member"), r.get("outsider"), r.get("nocontext")
    expected_member = up.get("memories")
    print("\nrestricted role (NOSUPERUSER NOBYPASSRLS) after re-upgrade:")
    print(f"  active member  sees {member} (database holds {expected_member})")
    print(f"  non-member     sees {outsider}")
    print(f"  no RLS context sees {nocontext}")

    if member != expected_member:
        failures.append(
            f"an active member saw {member} memories but the database holds "
            f"{expected_member} — the policy is over-restrictive after re-upgrade"
        )
    if outsider != "0":
        failures.append(
            f"a user with no membership saw {outsider} memories — isolation is "
            "broken after the round trip"
        )
    if nocontext != "0":
        failures.append(
            f"a connection with no RLS context saw {nocontext} memories — the "
            "policy is failing open"
        )

    # ── 5. the ownerless regression, phase by phase ───────────────────────
    print("")
    print("ownerless-workspace regression:")
    for phase, (expected, why) in REGRESSION_PHASES.items():
        report = ev / ("regression_%s.xml" % phase)
        try:
            suite = ET.parse(report).getroot()
        except ET.ParseError as exc:
            failures.append("regression report for %r is unreadable: %s" % (phase, exc))
            continue
        cases = list(suite.iter("testcase"))
        if len(cases) != 1:
            failures.append(
                "regression %r: expected exactly 1 test case, report has %d — "
                "the node was not collected as expected" % (phase, len(cases))
            )
            continue
        state = next(
            (k.tag for k in cases[0] if k.tag in {"skipped", "failure", "error"}),
            "passed",
        )
        ok = state == expected
        print("  %-10s %-8s (expected %-8s) %s  — %s"
              % (phase, state, expected, "ok" if ok else "MISMATCH", why))
        if ok:
            continue
        if state == "skipped":
            failures.append(
                "regression %r was SKIPPED, not executed — a skipped node is "
                "never evidence; check the OWNERLESS_COMPAT_* configuration" % phase
            )
        elif state == "error" and expected == "failure":
            failures.append(
                "regression %r ERRORED rather than failing its assertions. That "
                "is a broken harness, not a reproduction of the defect, and must "
                "not be counted as one" % phase
            )
        else:
            failures.append(
                "regression %r: expected %s, got %s — %s" % (phase, expected, state, why)
            )

    # ── 6. the migrations themselves SUCCEEDED ────────────────────────────
    # Distinct from any test result. A migration command that failed can never
    # be a passing phase, however the regression behaved afterwards.
    print("")
    print("revisions reached (a migration command failure is never a pass):")
    for phase, expected_rev in EXPECTED_REVISIONS.items():
        actual = (ev / ("revision_%s.txt" % phase)).read_text(encoding="utf-8").strip()
        ok = actual == expected_rev
        print("  %-10s %-16s (expected %s) %s"
              % (phase, actual or "<none>", expected_rev, "ok" if ok else "MISMATCH"))
        if not ok:
            failures.append(
                "after the %r migration the database is at %s, expected %s. The "
                "migration command did not land where it should have, so this "
                "phase cannot count as a successful migration however the "
                "regression behaved" % (phase, actual or "<nothing recorded>", expected_rev)
            )

    print(f"\nnote: {', '.join(DROPPED_BY_DESIGN)} are dropped by the 0007/0006 "
          "downgrades by design, so column data there is not expected to "
          "survive; row counts are.")

    if failures:
        print()
        for f in failures:
            print(f"::error::{f}")
        print(f"\nround-trip gate FAILED ({len(failures)} problem(s))")
        return 1

    print("\nround trip held: flags returned to the measured baseline, rows "
          "survived, and the restricted role is correctly constrained")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
