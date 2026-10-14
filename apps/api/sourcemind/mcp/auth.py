"""OAuth 2.1 resource-server token verification for the MCP interface.

Contract (MCP specification, Authorization + Security Considerations):

* the MCP server is a resource server; it never issues tokens;
* a token is accepted only if it was issued FOR THIS RESOURCE: its ``aud`` must contain the
  canonical resource URI. A token without that audience is rejected (no weakening: a Clerk
  session token, or a token minted for another server, never carries it);
* tokens are never passed through to other services.

Only JWT access tokens (Clerk's default format) are supported. Opaque tokens would need the
provider's verify endpoint, whose response fields are not documented, so they are rejected.

Identity: ``AccessToken.subject`` is the Clerk user id. Resolving it to a SourceMind user is a
separate, per-call step (``resolve_user``) so a deactivated or unknown user stops working at
once; nothing is provisioned from here.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from jose import JWTError, jwt
from mcp.server.auth.provider import AccessToken, TokenVerifier
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.core.dependencies import _fetch_jwks
from sourcemind.models.user import User

log = structlog.get_logger(__name__)


def _scopes(claims: dict[str, Any]) -> list[str]:
    raw = claims.get("scope")
    if isinstance(raw, str):
        return [s for s in raw.split() if s]
    scp = claims.get("scp")
    if isinstance(scp, list):
        return [str(s) for s in scp if s]
    if isinstance(scp, str):
        return [s for s in scp.split() if s]
    return []


async def verify_access_jwt(
    token: str, *, jwks_url: str, issuer: str, resource_url: str
) -> dict[str, Any] | None:
    """Return validated claims, or None for ANY failure (callers must not distinguish)."""
    try:
        header = jwt.get_unverified_header(token)
    except JWTError:
        return None
    if header.get("alg") != "RS256" or header.get("typ", "JWT") not in {"JWT", "at+jwt"}:
        return None
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        return None
    try:
        keys = await _fetch_jwks(jwks_url)
        key = next((k for k in keys if k.get("kid") == kid), None)
        if key is None:
            keys = await _fetch_jwks(jwks_url, force_refresh=True)
            key = next((k for k in keys if k.get("kid") == kid), None)
    except Exception as exc:  # network/JWKS outage: fail closed, never open
        log.warning("mcp.jwks_unavailable", error=type(exc).__name__)
        return None
    if key is None:
        return None
    try:
        return jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=issuer,
            audience=resource_url,
            options={
                "require_exp": True,
                "require_iat": True,
                "require_sub": True,
                "require_iss": True,
                "require_aud": True,
                "verify_aud": True,
            },
        )
    except JWTError:
        return None


class ClerkAccessTokenVerifier(TokenVerifier):
    """``TokenVerifier`` for Clerk-issued OAuth access tokens (JWT format)."""

    def __init__(self, *, jwks_url: str, issuer: str, resource_url: str) -> None:
        self._jwks_url = jwks_url
        self._issuer = issuer
        self._resource_url = resource_url

    async def verify_token(self, token: str) -> AccessToken | None:
        claims = await verify_access_jwt(
            token,
            jwks_url=self._jwks_url,
            issuer=self._issuer,
            resource_url=self._resource_url,
        )
        if claims is None:
            return None
        return AccessToken(
            token=token,
            client_id=str(claims.get("client_id") or claims.get("azp") or ""),
            scopes=_scopes(claims),
            expires_at=int(claims["exp"]),
            # The SDK compares this with its own resource URL when validate_token_resource is
            # on; the audience was already checked above, so copy the matched resource.
            resource=self._resource_url,
            subject=str(claims["sub"]),
            claims={k: v for k, v in claims.items() if k in {"iss", "sub", "aud", "exp", "iat"}},
        )


async def resolve_user(session: AsyncSession, subject: str) -> uuid.UUID | None:
    """Existing SourceMind user for a Clerk user id; never creates one."""
    row = await session.execute(select(User.id).where(User.clerk_id == subject))
    return row.scalar_one_or_none()
