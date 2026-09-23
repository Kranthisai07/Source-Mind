#!/usr/bin/env bash
# Row counts for the tables the seed populates, as name=count lines.
#
# Counted as the bootstrap superuser on purpose: this measures what is IN the
# database, not what a policy lets someone see. Visibility is a separate
# assertion made by the restricted role.
set -euo pipefail

OUT="${1:?output path required}"
: "${DB:?DB required}"
export PGPASSWORD="${PGPASSWORD:?PGPASSWORD required}"

psql -U sourcemind_bootstrap -d "$DB" -At -v ON_ERROR_STOP=1 -c "
SELECT 'organizations=' || (SELECT count(*) FROM organizations)
UNION ALL SELECT 'users=' || (SELECT count(*) FROM users)
UNION ALL SELECT 'workspaces=' || (SELECT count(*) FROM workspaces)
UNION ALL SELECT 'workspace_members=' || (SELECT count(*) FROM workspace_members)
UNION ALL SELECT 'memories=' || (SELECT count(*) FROM memories)
UNION ALL SELECT 'memory_conflicts=' || (SELECT count(*) FROM memory_conflicts)
UNION ALL SELECT 'memory_content_digest=' || COALESCE(
  md5(string_agg(content, '|' ORDER BY id)), 'none') FROM memories;
" > "$OUT"
