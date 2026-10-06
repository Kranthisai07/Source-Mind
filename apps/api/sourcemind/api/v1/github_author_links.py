"""
GitHub author links: admin-asserted identity links for synced artifacts (D-021).

GET    /v1/workspaces/:id/github-author-links                   — list links
PUT    /v1/workspaces/:id/github-author-links/:github_user_id   — create or correct
DELETE /v1/workspaces/:id/github-author-links/:github_user_id   — remove
GET    /v1/workspaces/:id/github-authors                        — authors seen in sync

A link maps GitHub's NUMERIC user id to an active member of the workspace. It
is asserted by a workspace admin and is NOT proof of account ownership. Only
links resolve authors; logins, names and e-mails are never matched. Creating,
correcting or deleting a link affects FUTURE syncs only — already-synced
memories are not back-filled. All routes require ADMINISTER.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import structlog
from fastapi import APIRouter, Path
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from sourcemind.core.dependencies import (
    CurrentUser,
    DBSession,
    WorkspacePermission,
    require_workspace_permission,
)
from sourcemind.core.exceptions import ValidationError
from sourcemind.schemas.common import ItemList

log = structlog.get_logger(__name__)

router = APIRouter(tags=["github-author-links"])

_PG_BIGINT_MAX = 9_223_372_036_854_775_807
GitHubUserId = Path(gt=0, le=_PG_BIGINT_MAX, description="GitHub numeric user id")

_LINK_COLUMNS = (
    "id, workspace_id, github_user_id, github_login, user_id, "
    "created_by_user_id, created_at, updated_by_user_id, updated_at"
)


class GitHubAuthorLinkUpsert(BaseModel):
    """Body for PUT. The GitHub id is in the path; no name/e-mail is accepted."""

    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID = Field(description="Active workspace member to credit")
    github_login: str | None = Field(
        default=None,
        max_length=100,
        description="Display label only; never used to resolve identity",
    )


class GitHubAuthorLinkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    github_user_id: int
    github_login: str | None
    user_id: uuid.UUID
    created_by_user_id: uuid.UUID
    created_at: datetime
    updated_by_user_id: uuid.UUID | None
    updated_at: datetime | None


class GitHubAuthorSeen(BaseModel):
    """A GitHub author observed in synced artifacts, with current link state."""

    github_user_id: int | None
    source_author: str | None
    kind: str | None
    artifact_count: int
    linked_user_id: uuid.UUID | None


async def _require_admin(db: DBSession, current_user: CurrentUser, workspace_id: uuid.UUID) -> None:
    await require_workspace_permission(
        db, current_user.user_id, workspace_id, WorkspacePermission.ADMINISTER
    )


@router.get(
    "/workspaces/{workspace_id}/github-author-links",
    response_model=ItemList[GitHubAuthorLinkResponse],
    summary="List admin-asserted GitHub author links",
)
async def list_github_author_links(
    workspace_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
) -> ItemList[GitHubAuthorLinkResponse]:
    await _require_admin(db, current_user, workspace_id)
    rows = (
        await db.execute(
            text(
                f"SELECT {_LINK_COLUMNS} FROM github_author_links "  # noqa: S608
                "WHERE workspace_id = CAST(:ws AS uuid) ORDER BY github_user_id"
            ),
            {"ws": str(workspace_id)},
        )
    ).fetchall()
    items = [GitHubAuthorLinkResponse.model_validate(row) for row in rows]
    return ItemList(items=items, total=len(items))


@router.put(
    "/workspaces/{workspace_id}/github-author-links/{github_user_id}",
    response_model=GitHubAuthorLinkResponse,
    summary="Create or correct a GitHub author link",
)
async def put_github_author_link(
    workspace_id: uuid.UUID,
    body: GitHubAuthorLinkUpsert,
    db: DBSession,
    current_user: CurrentUser,
    github_user_id: int = GitHubUserId,
) -> GitHubAuthorLinkResponse:
    """Create, or correct, the link. Affects future syncs only."""
    await _require_admin(db, current_user, workspace_id)

    member = (
        await db.execute(
            text(
                "SELECT 1 FROM workspace_members AS wm "
                "JOIN users AS u ON u.id = wm.user_id "
                "WHERE wm.workspace_id = CAST(:ws AS uuid) "
                "  AND wm.user_id = CAST(:uid AS uuid) "
                "  AND wm.status = 'active' "
                "  AND wm.departed_at IS NULL "
                "  AND u.deleted_at IS NULL"
            ),
            {"ws": str(workspace_id), "uid": str(body.user_id)},
        )
    ).first()
    if member is None:
        raise ValidationError("The link target must be an active member of this workspace.")

    # One statement, so a concurrent PUT cannot interleave a read and a write.
    # A correction stamps updated_by/at and never rewrites created_by/at.
    row = (
        await db.execute(
            text(
                "INSERT INTO github_author_links ("  # noqa: S608
                "  workspace_id, github_user_id, github_login, user_id, "
                "  created_by_user_id"
                ") VALUES ("
                "  CAST(:ws AS uuid), :gid, :login, CAST(:uid AS uuid), "
                "  CAST(:actor AS uuid)"
                ") ON CONFLICT (workspace_id, github_user_id) DO UPDATE SET "
                "  user_id = EXCLUDED.user_id, "
                "  github_login = EXCLUDED.github_login, "
                "  updated_by_user_id = CAST(:actor AS uuid), "
                "  updated_at = now() "
                f"RETURNING {_LINK_COLUMNS}"
            ),
            {
                "ws": str(workspace_id),
                "gid": github_user_id,
                "login": body.github_login,
                "uid": str(body.user_id),
                "actor": str(current_user.user_id),
            },
        )
    ).first()
    await db.commit()
    log.info(
        "github_author_link_upserted",
        workspace_id=str(workspace_id),
        github_user_id=github_user_id,
        user_id=str(body.user_id),
        actor_id=str(current_user.user_id),
    )
    return GitHubAuthorLinkResponse.model_validate(row)


@router.delete(
    "/workspaces/{workspace_id}/github-author-links/{github_user_id}",
    summary="Delete a GitHub author link",
)
async def delete_github_author_link(
    workspace_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
    github_user_id: int = GitHubUserId,
) -> dict[str, bool]:
    """Remove the link. Affects future syncs and not-yet-credited documents."""
    await _require_admin(db, current_user, workspace_id)
    deleted = (
        await db.execute(
            text(
                "DELETE FROM github_author_links "
                "WHERE workspace_id = CAST(:ws AS uuid) AND github_user_id = :gid "
                "RETURNING id"
            ),
            {"ws": str(workspace_id), "gid": github_user_id},
        )
    ).first()
    await db.commit()
    log.info(
        "github_author_link_deleted",
        workspace_id=str(workspace_id),
        github_user_id=github_user_id,
        deleted=deleted is not None,
        actor_id=str(current_user.user_id),
    )
    return {"deleted": deleted is not None}


@router.get(
    "/workspaces/{workspace_id}/github-authors",
    response_model=ItemList[GitHubAuthorSeen],
    summary="GitHub authors seen in synced artifacts, with link state",
)
async def list_github_authors(
    workspace_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
) -> ItemList[GitHubAuthorSeen]:
    await _require_admin(db, current_user, workspace_id)
    rows = (
        await db.execute(
            text(
                """
                SELECT
                    seen.github_user_id,
                    seen.source_author,
                    seen.kind,
                    seen.artifact_count,
                    gal.user_id AS linked_user_id
                FROM (
                    SELECT
                        CASE
                            WHEN al.metadata -> 'author' ->> 'kind' = 'github_account'
                             AND jsonb_typeof(al.metadata -> 'author' -> 'github_user_id')
                                 = 'number'
                            THEN (al.metadata -> 'author' ->> 'github_user_id')::bigint
                        END AS github_user_id,
                        al.source_author,
                        al.metadata -> 'author' ->> 'kind' AS kind,
                        COUNT(DISTINCT al.source_id) AS artifact_count
                    FROM artifact_links AS al
                    WHERE al.workspace_id = CAST(:ws AS uuid)
                      AND al.source_tool = 'github'
                    GROUP BY 1, 2, 3
                ) AS seen
                LEFT JOIN github_author_links AS gal
                  ON gal.workspace_id = CAST(:ws AS uuid)
                 AND gal.github_user_id = seen.github_user_id
                ORDER BY seen.artifact_count DESC, seen.source_author
                """
            ),
            {"ws": str(workspace_id)},
        )
    ).fetchall()
    items = [
        GitHubAuthorSeen(
            github_user_id=row.github_user_id,
            source_author=row.source_author,
            kind=row.kind,
            artifact_count=int(row.artifact_count),
            linked_user_id=row.linked_user_id,
        )
        for row in rows
    ]
    return ItemList(items=items, total=len(items))
