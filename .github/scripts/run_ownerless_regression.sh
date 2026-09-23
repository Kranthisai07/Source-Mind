#!/usr/bin/env bash
# Run the ownerless-workspace regression node once and record what happened.
#
# THE DISTINCTION THIS EXISTS TO PRESERVE
#
# At the previous 0007 definition this node is SUPPOSED to fail: that failure is
# the compatibility defect being demonstrated. It is evidence, not a problem.
#
# But "the test failed" is also what you get when the database is unreachable,
# the fixture is misconfigured, or the node was never collected. Counting those
# as a successful reproduction would let a broken harness masquerade as proof,
# and — at the downgrade step — would let a migration-command failure be
# recorded as a passing rollback. It must not.
#
# So this records the JUnit result rather than the shell exit code, and the gate
# distinguishes the four states pytest can produce for one node:
#
#   passed   the node ran and passed
#   failure  the node ran and its assertions failed   <- the expected defect
#   error    the node could not run (fixture/config)  <- NOT the defect
#   skipped  the node was never executed              <- NOT evidence at all
#
# Only `failure` counts as a reproduction. `error` and `skipped` never do.
#
# Usage: run_ownerless_regression.sh <phase> <evidence-dir>
set -uo pipefail

PHASE="${1:?phase name required}"
EVIDENCE="${2:?evidence directory required}"
NODE="tests/integration/test_ownerless_workspace_migration_real_db.py::test_ownerless_workspace_membership_survives_migration_round_trip"

mkdir -p "$EVIDENCE"
REPORT="$EVIDENCE/regression_${PHASE}.xml"

# -o addopts='' drops the coverage defaults from pyproject so the report holds
# the raw node result. -p no:cacheprovider keeps runs independent of each other.
python -m pytest -o addopts='' -q -p no:cacheprovider \
    --junitxml="$REPORT" "$NODE"
exit_code=$?

echo "regression_exit|${PHASE}|${exit_code}"
printf 'phase=%s\nexit=%s\n' "$PHASE" "$exit_code" > "$EVIDENCE/regression_${PHASE}.txt"

# Never fail here. Whether this outcome is correct depends on the phase, and
# that judgement belongs to the gate, which sees every phase at once.
exit 0
