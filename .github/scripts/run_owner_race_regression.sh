#!/usr/bin/env bash
# Run the concurrent owner-revocation regression once and record what happened.
#
# WHY THIS MIRRORS run_ownerless_regression.sh RATHER THAN CALLING pytest DIRECTLY
#
# The same distinction applies here, for a different defect. At migration
# 20260916_0008 this node is SUPPOSED to fail: two concurrent revocations both
# commit and the workspace is left with zero active owners. That failure IS the
# write-skew defect being demonstrated, and it is evidence.
#
# But "the test failed" is also what you get when the database is unreachable,
# the strict OWNER_RACE_* fixture guard rejected the target, or the node was
# never collected. Counting those as a successful reproduction would let a
# broken harness masquerade as proof that 0009 fixes something.
#
# So this records the JUnit result rather than the shell exit code, and the gate
# distinguishes the four states pytest can produce for one node:
#
#   passed   the node ran and passed                  <- 0009 serializes the check
#   failure  the node ran and its assertions failed   <- the expected defect at 0008
#   error    the node could not run (fixture/config)  <- NOT the defect
#   skipped  the node was never executed              <- NOT evidence at all
#
# Only `failure` counts as a reproduction. `error` and `skipped` never do.
#
# Usage: run_owner_race_regression.sh <phase> <evidence-dir>
set -uo pipefail

PHASE="${1:?phase name required}"
EVIDENCE="${2:?evidence directory required}"
NODE="tests/integration/test_security_foundation_real_db.py::test_concurrent_owner_revocations_preserve_one_active_owner"

mkdir -p "$EVIDENCE"
REPORT="$EVIDENCE/owner_race_${PHASE}.xml"

# -o addopts='' drops the coverage defaults from pyproject so the report holds
# the raw node result. -p no:cacheprovider keeps runs independent of each other.
python -m pytest -o addopts='' -q -p no:cacheprovider \
    --junitxml="$REPORT" "$NODE"
exit_code=$?

echo "owner_race_exit|${PHASE}|${exit_code}"
printf 'phase=%s\nexit=%s\n' "$PHASE" "$exit_code" > "$EVIDENCE/owner_race_${PHASE}.txt"

# Never fail here. Whether this outcome is correct depends on the phase, and
# that judgement belongs to the gate, which sees every phase at once.
exit 0
