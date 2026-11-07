#!/usr/bin/env bash
# Is the published frontend (the gh-pages branch) behind the web code on main?
#
# Publishing is a manual release step on purpose (docs/FRONTEND_PUBLISH.md, D-028: the
# bundle goes out last, after the API runs the matching commit). The cost is that nothing
# says when the site has fallen behind. This reads the commit a publish recorded
# ("Publish <sha12> (main, PR #n): ..." on the gh-pages head) and counts the commits that
# touched apps/web on main since then.
#
#   MAX_AGE_DAYS   how long unpublished web commits may wait before this fails (default 7)
#   FAIL_WHEN_STALE=1   exit 1 when the oldest unpublished web commit is older than that
#
# Needs a clone with full history and both branches fetched: origin/main and origin/gh-pages.
set -euo pipefail

MAIN="${MAIN_REF:-origin/main}"
PAGES="${PAGES_REF:-origin/gh-pages}"
MAX_AGE_DAYS="${MAX_AGE_DAYS:-7}"

say() {
  echo "$1"
  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then echo "$1" >> "$GITHUB_STEP_SUMMARY"; fi
}

subject="$(git log -1 --format=%s "$PAGES")"
published="$(printf '%s\n' "$subject" | sed -n 's/^Publish \([0-9a-f]\{7,40\}\).*/\1/p')"
if [ -z "$published" ]; then
  say "::error::the $PAGES head message does not start with 'Publish <sha>': $subject"
  exit 1
fi
if ! git merge-base --is-ancestor "$published" "$MAIN"; then
  say "::error::published commit $published is not on $MAIN"
  exit 1
fi

behind="$(git rev-list --count "$published..$MAIN" -- apps/web)"
if [ "$behind" -eq 0 ]; then
  say "Frontend is current: published $published, no apps/web commits on main since."
  exit 0
fi

oldest="$(git log --reverse --format=%cI "$published..$MAIN" -- apps/web | head -n 1)"
age_days="$(( ( $(date -u +%s) - $(date -u -d "$oldest" +%s) ) / 86400 ))"

say "Frontend is behind: published $published, $behind apps/web commit(s) on main since,"
say "the oldest $age_days day(s) ago ($oldest). Publish with docs/FRONTEND_PUBLISH.md after the API runs the matching commit."

if [ "${FAIL_WHEN_STALE:-0}" = "1" ] && [ "$age_days" -gt "$MAX_AGE_DAYS" ]; then
  say "::error::unpublished web changes have waited $age_days days (limit $MAX_AGE_DAYS)"
  exit 1
fi
echo "::warning::frontend is $behind web commit(s) behind main (oldest $age_days day(s))"
