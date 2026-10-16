"""Creation, listing, revocation, and lookup of workspace MCP API keys."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class CreatedKey:
    id: uuid.UUID
    name: str
    key: str
    key_prefix: str
    expires_at: datetime
    created_at: datetime


@dataclass(frozen=True)
class KeyRecord:
    id: uuid.UUID
    name: str
    key_prefix: str
    user_id: uuid.UUID
    creator_display_name: str
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    status: str


@dataclass(frozen=True)
class ResolvedKey:
    key_id: uuid.UUID
    workspace_id: uuid.UUID
    user_id: uuid.UUID


def hash_key(key: str) -> str:
    """Return the lowercase SHA-256 hex digest of a full MCP key."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def generate_key() -> tuple[str, str, str]:
    """Generate a 256-bit MCP key and its non-secret stored representations."""
    key = "smk_" + secrets.token_urlsafe(32)
    return key, key[:8], hash_key(key)


def _validated_name(name: str) -> str:
    value = name.strip()
    if not 1 <= len(value) <= 100:
        raise ValueError("name must contain between 1 and 100 characters")
    return value


def _validated_expiry(expires_in_days: int) -> int:
    if expires_in_days not in {30, 90, 365}:
        raise ValueError("expires_in_days must be one of 30, 90, or 365")
    return expires_in_days


def _status(*, expires_at: datetime, revoked_at: datetime | None) -> str:
    if revoked_at is not None:
        return "revoked"
    if expires_at <= datetime.now(UTC):
        return "expired"
    return "active"


def _record(row: object) -> KeyRecord:
    values = row._mapping  # type: ignore[attr-defined]
    return KeyRecord(
        id=values["id"],
        name=values["name"],
        key_prefix=values["key_prefix"],
        user_id=values["user_id"],
        creator_display_name=values["creator_display_name"],
        created_at=values["created_at"],
        expires_at=values["expires_at"],
        last_used_at=values["last_used_at"],
        revoked_at=values["revoked_at"],
        status=_status(expires_at=values["expires_at"], revoked_at=values["revoked_at"]),
    )


async def create_key(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    name: str,
    expires_in_days: int,
) -> CreatedKey:
    clean_name = _validated_name(name)
    days = _validated_expiry(expires_in_days)
    key, prefix, digest = generate_key()
    row = (
        await session.execute(
            text(
                "INSERT INTO mcp_api_keys "
                "(workspace_id, user_id, name, key_prefix, key_hash, expires_at) "
                "VALUES (CAST(:workspace_id AS uuid), CAST(:user_id AS uuid), :name, :prefix, "
                ":key_hash, now() + make_interval(days => CAST(:days AS integer))) "
                "RETURNING id, name, key_prefix, expires_at, created_at"
            ),
            {
                "workspace_id": str(workspace_id),
                "user_id": str(user_id),
                "name": clean_name,
                "prefix": prefix,
                "key_hash": digest,
                "days": days,
            },
        )
    ).one()
    await session.execute(
        text(
            "INSERT INTO mcp_key_events (key_id, workspace_id, actor_user_id, event) "
            "VALUES (CAST(:key_id AS uuid), CAST(:workspace_id AS uuid), "
            "CAST(:actor_user_id AS uuid), 'created')"
        ),
        {
            "key_id": str(row.id),
            "workspace_id": str(workspace_id),
            "actor_user_id": str(user_id),
        },
    )
    return CreatedKey(
        id=row.id,
        name=row.name,
        key=key,
        key_prefix=row.key_prefix,
        expires_at=row.expires_at,
        created_at=row.created_at,
    )


async def list_keys(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    is_admin: bool,
) -> list[KeyRecord]:
    rows = (
        await session.execute(
            text(
                "SELECT k.id, k.name, k.key_prefix, k.user_id, "
                "COALESCE(u.display_name, u.email) AS creator_display_name, "
                "k.created_at, k.expires_at, k.last_used_at, k.revoked_at "
                "FROM mcp_api_keys AS k JOIN users AS u ON u.id = k.user_id "
                "WHERE k.workspace_id = CAST(:workspace_id AS uuid) "
                "AND (CAST(:is_admin AS boolean) OR k.user_id = CAST(:user_id AS uuid)) "
                "ORDER BY k.created_at DESC, k.id"
            ),
            {
                "workspace_id": str(workspace_id),
                "user_id": str(user_id),
                "is_admin": is_admin,
            },
        )
    ).all()
    return [_record(row) for row in rows]


async def revoke_key(
    session: AsyncSession,
    *,
    key_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    is_admin: bool,
) -> KeyRecord | None:
    row = (
        await session.execute(
            text(
                "SELECT k.id, k.name, k.key_prefix, k.user_id, "
                "COALESCE(u.display_name, u.email) AS creator_display_name, "
                "k.created_at, k.expires_at, k.last_used_at, k.revoked_at "
                "FROM mcp_api_keys AS k JOIN users AS u ON u.id = k.user_id "
                "WHERE k.id = CAST(:key_id AS uuid) "
                "AND k.workspace_id = CAST(:workspace_id AS uuid) "
                "AND (CAST(:is_admin AS boolean) OR k.user_id = CAST(:actor_user_id AS uuid)) "
                "FOR UPDATE OF k"
            ),
            {
                "key_id": str(key_id),
                "workspace_id": str(workspace_id),
                "actor_user_id": str(actor_user_id),
                "is_admin": is_admin,
            },
        )
    ).first()
    if row is None:
        return None
    if row.revoked_at is not None:
        return _record(row)

    revoked = (
        await session.execute(
            text(
                "UPDATE mcp_api_keys SET revoked_at = now(), "
                "revoked_by = CAST(:actor_user_id AS uuid) "
                "WHERE id = CAST(:key_id AS uuid) "
                "RETURNING revoked_at"
            ),
            {"actor_user_id": str(actor_user_id), "key_id": str(key_id)},
        )
    ).one()
    await session.execute(
        text(
            "INSERT INTO mcp_key_events (key_id, workspace_id, actor_user_id, event) "
            "VALUES (CAST(:key_id AS uuid), CAST(:workspace_id AS uuid), "
            "CAST(:actor_user_id AS uuid), 'revoked')"
        ),
        {
            "key_id": str(key_id),
            "workspace_id": str(workspace_id),
            "actor_user_id": str(actor_user_id),
        },
    )
    return replace(_record(row), revoked_at=revoked.revoked_at, status="revoked")


async def _set_lookup_hash(session: AsyncSession, digest: str) -> None:
    await session.execute(
        text("SELECT set_config('app.mcp_key_hash', CAST(:key_hash AS text), true)"),
        {"key_hash": digest},
    )


async def _clear_lookup_hash(session: AsyncSession) -> None:
    await session.execute(text("SELECT set_config('app.mcp_key_hash', '', true)"))


async def resolve_key(session: AsyncSession, key: str) -> ResolvedKey | None:
    digest = hash_key(key)
    await _set_lookup_hash(session, digest)
    try:
        row = (
            await session.execute(
                text(
                    "SELECT id, workspace_id, user_id FROM mcp_api_keys "
                    "WHERE key_hash = CAST(:key_hash AS char(64)) "
                    "AND revoked_at IS NULL AND expires_at > now()"
                ),
                {"key_hash": digest},
            )
        ).first()
    finally:
        await _clear_lookup_hash(session)
    if row is None:
        return None
    return ResolvedKey(key_id=row.id, workspace_id=row.workspace_id, user_id=row.user_id)


async def touch_key(session: AsyncSession, key_id: uuid.UUID, key: str) -> None:
    digest = hash_key(key)
    await _set_lookup_hash(session, digest)
    try:
        await session.execute(
            text(
                "UPDATE mcp_api_keys SET last_used_at = now() "
                "WHERE id = CAST(:key_id AS uuid) "
                "AND key_hash = CAST(:key_hash AS char(64)) "
                "AND revoked_at IS NULL AND expires_at > now() "
                "AND (last_used_at IS NULL "
                "OR last_used_at < now() - interval '5 minutes')"
            ),
            {"key_id": str(key_id), "key_hash": digest},
        )
    finally:
        await _clear_lookup_hash(session)
