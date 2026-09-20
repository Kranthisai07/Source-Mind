#!/usr/bin/env bash
# Rollback compatibility for connector types 0005 does not know about.
#
# THE DEFECT THIS COVERS
#
# Migration 0006 widened connector_type to include 'slack' and 'notion'. Its
# downgrade restored the old two-value CHECK without transforming the rows, and
# PostgreSQL validates a new CHECK against existing rows — so one stored Slack
# connector aborted `alembic downgrade 20250817_0005` PART WAY THROUGH, leaving
# the database stranded between revisions.
#
# The correction does NOT make the rollback succeed. It makes it refuse, before
# anything destructive runs, and say why. That distinction is the whole point,
# so this asserts the refusal rather than an exit code:
#
#   - a nonzero exit alone is not evidence. Any crash produces one.
#   - the guard MESSAGE must be present, so we know it was the guard and not a
#     connection failure, a typo, or an unrelated error.
#   - the revision must still be head afterwards, proving nothing in between
#     ran.
#   - both fixture rows must still exist, proving no data was deleted or
#     relabelled to make the rollback possible.
#
# Then the supported path is exercised: a downgrade to 0008, whose schema still
# accepts these rows, must SUCCEED with the data intact, and a re-upgrade must
# return to head with the data still intact.
#
# Usage: check_connector_rollback.sh <evidence-dir>
set -uo pipefail

EVIDENCE="${1:?evidence directory required}"
: "${DB:?DB required}"
: "${E2E_DB_OWNER_PASSWORD:?owner password required}"
: "${E2E_DB_BOOTSTRAP_PASSWORD:?bootstrap password required}"

OUT="$EVIDENCE/connector_rollback.txt"
mkdir -p "$EVIDENCE"
: > "$OUT"

# Fixed so step 6 can delete exactly these two rows and nothing else.
CONNECTOR_ID="00000000-0000-4000-8000-00000000c001"
LOG_ID="00000000-0000-4000-8000-00000000c002"

export DATABASE_URL="postgresql+asyncpg://sourcemind_owner:${E2E_DB_OWNER_PASSWORD}@127.0.0.1:5432/${DB}"

boot() { PGPASSWORD="$E2E_DB_BOOTSTRAP_PASSWORD" psql -U sourcemind_bootstrap -d "$DB" -At -v ON_ERROR_STOP=1 "$@"; }

rows() {
    local c l
    c=$(boot -c "SELECT count(*) FROM connector_configs WHERE id = '${CONNECTOR_ID}'")
    l=$(boot -c "SELECT count(*) FROM connector_sync_logs WHERE id = '${LOG_ID}'")
    echo "${c}|${l}"
}

revision() {
    boot -c "SELECT version_num FROM alembic_version" | head -1
}

# ── 1. the incompatible fixture ──────────────────────────────────────────────
# Attached to whichever workspace the seed produced; the guard is about
# connector_type, not about which workspace owns the row.
boot -c "
INSERT INTO connector_configs (id, workspace_id, connector_type, display_name, config, status)
VALUES ('${CONNECTOR_ID}', (SELECT id FROM workspaces ORDER BY id LIMIT 1),
        'slack', 'rollback-fixture', '{}'::jsonb, 'active');
INSERT INTO connector_sync_logs (id, connector_id, sync_type, status)
VALUES ('${LOG_ID}', '${CONNECTOR_ID}', 'full', 'completed');
" > /dev/null
echo "fixture_rows_inserted=$(rows)" >> "$OUT"

# ── 2. the guarded refusal ───────────────────────────────────────────────────
set +e
guard_output=$(cd apps/api && alembic downgrade 20250817_0005 2>&1)
guard_exit=$?
set -e
echo "guard_exit=${guard_exit}" >> "$OUT"
if printf '%s' "$guard_output" | grep -q 'connector types unsupported by 0005'; then
    echo "guard_message=present" >> "$OUT"
else
    echo "guard_message=absent" >> "$OUT"
fi

# ── 3. nothing moved, nothing was lost ───────────────────────────────────────
echo "revision_after_guard=$(revision)" >> "$OUT"
echo "rows_after_guard=$(rows)" >> "$OUT"

# ── 4. the supported destination still works ─────────────────────────────────
set +e
(cd apps/api && alembic downgrade 20260916_0008 > /dev/null 2>&1)
down_exit=$?
set -e
echo "downgrade_0008_exit=${down_exit}" >> "$OUT"
echo "revision_after_0008=$(revision)" >> "$OUT"
echo "rows_after_0008=$(rows)" >> "$OUT"

# ── 5. and it comes back ─────────────────────────────────────────────────────
set +e
(cd apps/api && alembic upgrade head > /dev/null 2>&1)
up_exit=$?
set -e
echo "reupgrade_exit=${up_exit}" >> "$OUT"
echo "revision_after_reupgrade=$(revision)" >> "$OUT"
echo "rows_after_reupgrade=$(rows)" >> "$OUT"

# ── 6. remove ONLY the two synthetic rows ────────────────────────────────────
# By primary key, so nothing the seed created can be caught by this.
boot -c "
DELETE FROM connector_sync_logs WHERE id = '${LOG_ID}';
DELETE FROM connector_configs  WHERE id = '${CONNECTOR_ID}';
" > /dev/null
echo "rows_after_cleanup=$(rows)" >> "$OUT"

echo "connector rollback evidence:"
cat "$OUT"

# The judgement belongs to the gate, which sees every line at once.
exit 0
