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
    "head": "20260916_0009",
    "reupgrade": "20260916_0009",
    # The owner-race track, which runs after every snapshot.
    "race_head": "20260916_0009",
    "race_prefix": "20260916_0008",
    "race_restored": "20260916_0009",
}

# Per-phase expectations for the concurrent owner-revocation regression.
#
# Same discipline as REGRESSION_PHASES, independent defect. 0009 adds a
# FOR UPDATE lock on the workspace row before the last-owner check, so at
# 0008 two concurrent revocations both commit and the workspace is left
# with zero active owners. That failure is the write skew, and only an
# EXECUTED assertion failure demonstrates it — an error means the strict
# OWNER_RACE_* fixture guard rejected the target or the harness broke,
# and a skip means nothing ran at all.
# The rollback-compatibility contract, as exact evidence values.
#
# 0006 widened connector_type; its downgrade restored the narrow CHECK
# without transforming rows, so one Slack row aborted the rollback PART
# WAY THROUGH. The correction does not make that rollback succeed — it
# makes it refuse before anything destructive runs. So a nonzero exit is
# necessary but nowhere near sufficient: any crash produces one. The guard
# message proves it was the guard, the revision proves nothing in between
# ran, and the surviving rows prove no data was deleted or relabelled to
# make the rollback possible.
CONNECTOR_ROLLBACK = {
    "fixture_rows_inserted": "1|1",
    "guard_message": "present",
    "revision_after_guard": "20260916_0009",
    "rows_after_guard": "1|1",
    "downgrade_0008_exit": "0",
    "revision_after_0008": "20260916_0008",
    "rows_after_0008": "1|1",
    "reupgrade_exit": "0",
    "revision_after_reupgrade": "20260916_0009",
    "rows_after_reupgrade": "1|1",
    "rows_after_cleanup": "0|0",
}

OWNER_RACE_PHASES = {
    "head": ("passed", "0009 must hold after the full round trip"),
    "prefix": ("failure", "removing 0009 must bring the write skew back"),
    "restored": ("passed", "re-applying 0009 must restore serialization"),
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
    "connector_rollback.txt": "connector rollback compatibility",
    "revision_race_head.txt": "revision for the owner-race check at head",
    "revision_race_prefix.txt": "revision after downgrading to 0008",
    "revision_race_restored.txt": "revision after re-applying 0009",
    "owner_race_head.xml": "owner-race result at head",
    "owner_race_prefix.xml": "owner-race result at 0008",
    "owner_race_restored.xml": "owner-race result after re-applying 0009",
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


def check_phases(ev, phases, report_prefix, label, config_hint):
    """Judge one regression track, phase by phase, from its JUnit reports.

    Shared by both tracks because the discipline is identical: the state is
    read from the report rather than from any step exit code, and `error`
    and `skipped` are never accepted as a reproduction of a defect.
    """
    print("")
    print("%s:" % label)
    for phase, (expected, why) in phases.items():
        report = ev / ("%s_%s.xml" % (report_prefix, phase))
        try:
            suite = ET.parse(report).getroot()
        except ET.ParseError as exc:
            failures.append("%s report for %r is unreadable: %s" % (label, phase, exc))
            continue
        cases = list(suite.iter("testcase"))
        if len(cases) != 1:
            failures.append(
                "%s %r: expected exactly 1 test case, report has %d — "
                "the node was not collected as expected" % (label, phase, len(cases))
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
                "%s %r was SKIPPED, not executed — a skipped node is never "
                "evidence; check the %s configuration" % (label, phase, config_hint)
            )
        elif state == "error" and expected == "failure":
            failures.append(
                "%s %r ERRORED rather than failing its assertions. That is a "
                "broken harness, not a reproduction of the defect, and must "
                "not be counted as one" % (label, phase)
            )
        else:
            failures.append(
                "%s %r: expected %s, got %s — %s" % (label, phase, expected, state, why)
            )


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

    # ── 5. both regression tracks, phase by phase ─────────────────────────
    check_phases(ev, REGRESSION_PHASES, "regression",
                 "ownerless-workspace regression", "OWNERLESS_COMPAT_*")
    check_phases(ev, OWNER_RACE_PHASES, "owner_race",
                 "concurrent owner-revocation regression", "OWNER_RACE_*")

    # ── 5b. rollback with an incompatible connector ───────────────────────
    print("")
    print("connector rollback compatibility:")
    cr = parse_kv(ev / "connector_rollback.txt")

    # Checked separately because "not zero" is the assertion, not a value.
    guard_exit = cr.get("guard_exit", "")
    guard_refused = guard_exit not in ("", "0")
    print("  %-26s %-16s (expected nonzero) %s"
          % ("guard_exit", guard_exit or "<none>",
             "ok" if guard_refused else "MISMATCH"))
    if not guard_refused:
        failures.append(
            "the rollback to 0005 did NOT fail with an incompatible connector "
            "present (guard_exit=%s). The guard did not fire, so either the "
            "fixture did not land or the refusal is gone" % (guard_exit or "<none>")
        )

    for key, expected in CONNECTOR_ROLLBACK.items():
        actual = cr.get(key, "<missing>")
        ok = actual == expected
        print("  %-26s %-16s (expected %-16s) %s"
              % (key, actual, expected, "ok" if ok else "MISMATCH"))
        if not ok:
            failures.append(
                "connector rollback: %s is %s, expected %s"
                % (key, actual, expected)
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
