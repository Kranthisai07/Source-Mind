"""Contributor activity insights derived from stored GitHub artifacts."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.core.exceptions import ValidationError

_MAX_WINDOW = timedelta(days=366)
_DEFAULT_WINDOW = timedelta(days=90)
_COUNT_FIELDS = (
    "commits",
    "pull_requests",
    "reviews",
    "review_comments",
    "issues",
    "comments",
    "discussions",
    "shared",
    "total",
)
_NOTES = [
    "Activity counts are not a measure of quality.",
    "Results cover only the imported window.",
    "Lines of code are unavailable because diffs are not stored.",
    "Bots are excluded during import.",
    "Shared and unresolved contributions are reported separately.",
]

_INSIGHTS_SQL = text(
    """
    WITH params AS (
        SELECT
            CAST(:workspace_id AS uuid) AS workspace_id,
            CAST(:connector_id AS uuid) AS connector_id,
            CAST(:repo AS text) AS repo,
            CAST(:since AS timestamptz) AS since_at,
            CAST(:until AS timestamptz) AS until_at,
            CAST(:include_disconnected AS boolean) AS include_disconnected
    ),
    eligible_connectors AS (
        SELECT c.id, c.config
        FROM connector_configs AS c
        CROSS JOIN params AS p
        WHERE c.workspace_id = p.workspace_id
          AND c.connector_type = 'github'
          AND (p.connector_id IS NULL OR c.id = p.connector_id)
          AND (p.include_disconnected OR c.status <> 'disconnected')
          AND (
              p.repo IS NULL
              OR EXISTS (
                  SELECT 1
                  FROM jsonb_array_elements_text(
                      COALESCE(c.config -> 'repos', '[]'::jsonb)
                  ) AS configured(repo)
                  WHERE lower(configured.repo) = lower(p.repo)
              )
          )
    ),
    configured_repos AS (
        SELECT DISTINCT configured.repo
        FROM eligible_connectors AS c
        CROSS JOIN params AS p
        CROSS JOIN LATERAL jsonb_array_elements_text(
            COALESCE(c.config -> 'repos', '[]'::jsonb)
        ) AS configured(repo)
        WHERE p.repo IS NULL OR lower(configured.repo) = lower(p.repo)
    ),
    coverage AS (
        SELECT
            (SELECT count(*) FROM eligible_connectors) AS connectors,
            (SELECT count(*) FROM configured_repos) AS repos,
            count(cur.*) AS scopes_total,
            count(cur.*) FILTER (WHERE NOT cur.listing_complete) AS scopes_incomplete
        FROM eligible_connectors AS c
        CROSS JOIN params AS p
        LEFT JOIN connector_sync_cursors AS cur
          ON cur.connector_id = c.id
         AND cur.workspace_id = p.workspace_id
         AND (p.repo IS NULL OR lower(cur.repo) = lower(p.repo))
    ),
    filtered_links AS (
        SELECT a.*
        FROM artifact_links AS a
        JOIN artifact_heads AS h
          ON h.workspace_id = a.workspace_id
         AND h.source_tool = a.source_tool
         AND h.source_type = a.source_type
         AND h.source_id = a.source_id
         AND h.published_seq = a.revision_seq
        JOIN eligible_connectors AS c ON c.id = a.connector_id
        CROSS JOIN params AS p
        WHERE a.workspace_id = p.workspace_id
          AND a.source_tool = 'github'
          AND a.source_timestamp >= p.since_at
          AND a.source_timestamp < p.until_at
          AND (
              p.repo IS NULL
              OR lower(
                  split_part(a.source_id, '/', 1) || '/' || split_part(a.source_id, '/', 2)
              ) = lower(p.repo)
          )
    ),
    artifacts AS (
        SELECT DISTINCT ON (source_tool, source_type, source_id)
            workspace_id,
            source_tool,
            source_type,
            source_id,
            source_author,
            source_timestamp,
            metadata
        FROM filtered_links
        ORDER BY
            source_tool,
            source_type,
            source_id,
            (memory_id IS NULL) DESC,
            created_at,
            id
    ),
    artifact_memories AS (
        SELECT
            a.source_tool,
            a.source_type,
            a.source_id,
            count(DISTINCT a.memory_id) FILTER (
                WHERE a.memory_id IS NOT NULL AND m.deleted_at IS NULL
            ) AS live_memories
        FROM filtered_links AS a
        LEFT JOIN memories AS m
          ON m.id = a.memory_id
         AND m.workspace_id = a.workspace_id
        GROUP BY a.source_tool, a.source_type, a.source_id
    ),
    single_entries AS (
        SELECT
            CASE
                WHEN a.metadata -> 'author' ->> 'kind' = 'github_account'
                 AND a.metadata -> 'author' ->> 'github_user_id' ~ '^[1-9][0-9]*$'
                THEN 'github:' || (a.metadata -> 'author' ->> 'github_user_id')
                ELSE 'name:' || COALESCE(
                    NULLIF(a.metadata -> 'author' ->> 'login', ''),
                    NULLIF(a.source_author, '')
                )
            END AS contributor_key,
            CASE
                WHEN a.metadata -> 'author' ->> 'kind' = 'github_account'
                 AND a.metadata -> 'author' ->> 'github_user_id' ~ '^[1-9][0-9]*$'
                THEN (a.metadata -> 'author' ->> 'github_user_id')::bigint
            END AS github_user_id,
            COALESCE(
                NULLIF(a.metadata -> 'author' ->> 'login', ''),
                NULLIF(a.source_author, '')
            ) AS label,
            CASE
                WHEN a.metadata -> 'author' ->> 'kind' = 'github_account'
                THEN COALESCE(
                    NULLIF(a.metadata -> 'author' ->> 'login', ''),
                    NULLIF(a.source_author, '')
                )
            END AS login,
            a.source_type,
            a.source_timestamp,
            memories.live_memories,
            false AS shared
        FROM artifacts AS a
        JOIN artifact_memories AS memories
          ON memories.source_tool = a.source_tool
         AND memories.source_type = a.source_type
         AND memories.source_id = a.source_id
        WHERE COALESCE(a.metadata ->> 'authorship', 'single') <> 'shared'
          AND (
              (
                  a.metadata -> 'author' ->> 'kind' = 'github_account'
                  AND a.metadata -> 'author' ->> 'github_user_id' ~ '^[1-9][0-9]*$'
              )
              OR COALESCE(
                  NULLIF(a.metadata -> 'author' ->> 'login', ''),
                  NULLIF(a.source_author, '')
              ) IS NOT NULL
          )
    ),
    shared_entries AS (
        SELECT
            'name:' || author.label AS contributor_key,
            NULL::bigint AS github_user_id,
            author.label,
            NULL::text AS login,
            a.source_type,
            a.source_timestamp,
            memories.live_memories,
            true AS shared
        FROM artifacts AS a
        JOIN artifact_memories AS memories
          ON memories.source_tool = a.source_tool
         AND memories.source_type = a.source_type
         AND memories.source_id = a.source_id
        CROSS JOIN LATERAL (
            SELECT DISTINCT value AS label
            FROM jsonb_array_elements_text(
                COALESCE(a.metadata -> 'source_authors', '[]'::jsonb)
            )
            WHERE NULLIF(value, '') IS NOT NULL
        ) AS author
        WHERE a.metadata ->> 'authorship' = 'shared'
    ),
    entries AS (
        SELECT * FROM single_entries
        UNION ALL
        SELECT * FROM shared_entries
    ),
    contributor_counts AS (
        SELECT
            contributor_key,
            github_user_id,
            (array_agg(login ORDER BY source_timestamp DESC, login DESC)
                FILTER (WHERE login IS NOT NULL))[1] AS login,
            (array_agg(label ORDER BY source_timestamp DESC, label DESC)
                FILTER (WHERE label IS NOT NULL))[1] AS label,
            count(*) FILTER (WHERE NOT shared AND source_type = 'commit') AS commits,
            count(*) FILTER (WHERE NOT shared AND source_type = 'pull_request')
                AS pull_requests,
            count(*) FILTER (WHERE NOT shared AND source_type = 'pull_review') AS reviews,
            count(*) FILTER (WHERE NOT shared AND source_type = 'pull_review_comment')
                AS review_comments,
            count(*) FILTER (WHERE NOT shared AND source_type = 'issue') AS issues,
            count(*) FILTER (WHERE NOT shared AND source_type = 'issue_comment') AS comments,
            count(*) FILTER (WHERE NOT shared AND source_type = 'discussion') AS discussions,
            count(*) FILTER (WHERE shared) AS shared,
            count(*) FILTER (
                WHERE NOT shared AND source_type IN (
                    'commit',
                    'pull_request',
                    'pull_review',
                    'pull_review_comment',
                    'issue',
                    'issue_comment',
                    'discussion'
                )
            ) AS total,
            min(source_timestamp) AS first_activity,
            max(source_timestamp) AS last_activity,
            sum(live_memories) AS live_memories
        FROM entries
        WHERE contributor_key IS NOT NULL
        GROUP BY contributor_key, github_user_id
    ),
    contributors AS (
        SELECT
            counts.*,
            COALESCE(counts.login, counts.label, counts.contributor_key) AS name,
            links.user_id AS linked_user_id,
            users.display_name AS linked_user_display_name,
            counts.github_user_id IS NULL AS unresolved
        FROM contributor_counts AS counts
        CROSS JOIN params AS p
        LEFT JOIN github_author_links AS links
          ON links.workspace_id = p.workspace_id
         AND links.github_user_id = counts.github_user_id
        LEFT JOIN users ON users.id = links.user_id
    )
    SELECT
        coverage.connectors,
        coverage.repos,
        coverage.scopes_total,
        coverage.scopes_incomplete,
        contributors.contributor_key,
        contributors.name,
        contributors.login,
        contributors.github_user_id,
        contributors.commits,
        contributors.pull_requests,
        contributors.reviews,
        contributors.review_comments,
        contributors.issues,
        contributors.comments,
        contributors.discussions,
        contributors.shared,
        contributors.total,
        contributors.first_activity,
        contributors.last_activity,
        contributors.live_memories,
        contributors.linked_user_id,
        contributors.linked_user_display_name,
        contributors.unresolved
    FROM coverage
    LEFT JOIN contributors ON true
    ORDER BY
        contributors.total DESC NULLS LAST,
        contributors.last_activity DESC NULLS LAST,
        lower(contributors.name),
        contributors.name,
        contributors.contributor_key
    """
)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def github_insights(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    connector_id: uuid.UUID | None = None,
    repo: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    include_disconnected: bool = False,
) -> dict[str, Any]:
    """Return contributor activity from published GitHub artifacts already stored."""
    window_until = _as_utc(until) if until is not None else datetime.now(UTC)
    window_since = _as_utc(since) if since is not None else window_until - _DEFAULT_WINDOW
    if window_since >= window_until:
        raise ValidationError("The insights 'since' value must be earlier than 'until'.")
    if window_until - window_since > _MAX_WINDOW:
        raise ValidationError("The insights window cannot exceed 366 days.")

    rows = (
        await session.execute(
            _INSIGHTS_SQL,
            {
                "workspace_id": str(workspace_id),
                "connector_id": str(connector_id) if connector_id is not None else None,
                "repo": repo,
                "since": window_since,
                "until": window_until,
                "include_disconnected": include_disconnected,
            },
        )
    ).mappings().all()

    first = rows[0]
    coverage = {
        "connectors": int(first["connectors"]),
        "repos": int(first["repos"]),
        "scopes_total": int(first["scopes_total"]),
        "scopes_incomplete": int(first["scopes_incomplete"]),
    }
    totals = dict.fromkeys(_COUNT_FIELDS, 0)
    contributors: list[dict[str, Any]] = []
    for row in rows:
        if row["contributor_key"] is None:
            continue
        contributor = {
            "name": row["name"],
            "login": row["login"],
            "github_user_id": row["github_user_id"],
            "linked_user": (
                {
                    "user_id": row["linked_user_id"],
                    "display_name": row["linked_user_display_name"],
                }
                if row["linked_user_id"] is not None
                else None
            ),
            "unresolved": row["unresolved"],
            "commits": int(row["commits"]),
            "pull_requests": int(row["pull_requests"]),
            "reviews": int(row["reviews"]),
            "review_comments": int(row["review_comments"]),
            "issues": int(row["issues"]),
            "comments": int(row["comments"]),
            "discussions": int(row["discussions"]),
            "shared": int(row["shared"]),
            "total": int(row["total"]),
            "first_activity": row["first_activity"],
            "last_activity": row["last_activity"],
            "live_memories": int(row["live_memories"] or 0),
        }
        contributors.append(contributor)
        for field in _COUNT_FIELDS:
            totals[field] += contributor[field]

    return {
        "window": {"since": window_since, "until": window_until},
        "coverage": coverage,
        "contributors": contributors,
        "totals": totals,
        "notes": list(_NOTES),
    }
