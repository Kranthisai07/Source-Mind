#!/usr/bin/env bash
# Capture relrowsecurity / relforcerowsecurity for the thirteen tables named in
# SECURITY_FOUNDATION_ROLLOUT.md's verification query, in a stable order and a
# stable format, so two captures can be compared with a plain diff.
#
# Tables absent at a given revision are recorded as "<name>|absent" rather than
# omitted: a table vanishing between two captures is a difference worth seeing,
# not a row to silently drop.
set -euo pipefail

OUT="${1:?output path required}"
: "${DB:?DB required}"
export PGPASSWORD="${PGPASSWORD:?PGPASSWORD required}"

psql -U sourcemind_bootstrap -d "$DB" -At -v ON_ERROR_STOP=1 -c "
WITH expected(relname) AS (
  VALUES ('workspaces'), ('workspace_members'), ('documents'), ('memories'),
         ('attributions'), ('attribution_edits'), ('memory_relations'),
         ('memory_conflicts'), ('connector_configs'), ('connector_sync_logs'),
         ('artifact_links'), ('handoff_records'), ('handoff_assignments')
)
SELECT e.relname || '|' ||
       CASE WHEN c.oid IS NULL THEN 'absent'
            ELSE c.relrowsecurity::text || '|' || c.relforcerowsecurity::text
       END
FROM expected AS e
LEFT JOIN pg_class AS c
  ON c.relname = e.relname
 AND c.relnamespace = 'public'::regnamespace
ORDER BY e.relname;
" > "$OUT"

lines=$(grep -c . "$OUT" || true)
if [ "$lines" -ne 13 ]; then
  echo "::error::expected 13 RLS rows, captured $lines"
  exit 1
fi
