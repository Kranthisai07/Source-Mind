"""GitHub App authentication — JWT generation and installation token exchange.

Flow:
  1. Generate a short-lived JWT (10-minute expiry) signed with the App private key (RS256).
  2. Exchange the JWT for an installation access token via GitHub REST API.
  3. Cache the installation token in Redis for 50 minutes (GitHub tokens expire at 60 min).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import httpx
import jwt as pyjwt

if TYPE_CHECKING:
    from redis.asyncio import Redis

_GITHUB_API_BASE = "https://api.github.com"
_TOKEN_CACHE_TTL = 50 * 60  # 50 minutes in seconds
_JWT_EXPIRY_SECONDS = 600    # 10 minutes (GitHub maximum)


class GitHubAppAuth:
    """Manages GitHub App authentication for a single installation.

    Args:
        app_id: GitHub App ID (numeric string or int).
        private_key_pem: Contents of the .pem private key file (RS256).
        installation_id: GitHub App installation ID.
        redis_client: Async Redis client for token caching.
    """

    def __init__(
        self,
        app_id: str | int,
        private_key_pem: str,
        installation_id: str | int,
        redis_client: "Redis",
    ) -> None:
        self._app_id = str(app_id)
        self._private_key_pem = private_key_pem
        self._installation_id = str(installation_id)
        self._redis = redis_client
        self._cache_key = f"gh_install_token:{self._installation_id}"

    # ── Public API ────────────────────────────────────────────────────────────

    async def get_token(self) -> str:
        """Return a valid installation access token, using the Redis cache.

        If a cached token exists it is returned immediately; otherwise a new
        JWT is generated, exchanged for an installation token, and cached.
        When ``redis_client`` is ``None`` the cache is bypassed entirely.
        """
        if self._redis is not None:
            cached = await self._redis.get(self._cache_key)
            if cached:
                return cached

        jwt_token = self._generate_jwt()
        token = await self._exchange_for_installation_token(jwt_token)

        if self._redis is not None:
            await self._redis.setex(self._cache_key, _TOKEN_CACHE_TTL, token)
        return token

    async def invalidate_cache(self) -> None:
        """Force removal of the cached token (useful after 401 responses)."""
        if self._redis is not None:
            await self._redis.delete(self._cache_key)

    # ── Internals ─────────────────────────────────────────────────────────────

    def _generate_jwt(self) -> str:
        """Generate a 10-minute RS256 JWT for the GitHub App.

        GitHub requires:
          - ``iat`` set slightly in the past (−60 s) to account for clock skew.
          - ``exp`` no more than 10 minutes from now.
          - ``iss`` equal to the App ID string.
        """
        now = int(time.time())
        payload = {
            "iat": now - 60,                          # issued-at (60 s in past)
            "exp": now + _JWT_EXPIRY_SECONDS - 60,    # expiry
            "iss": self._app_id,
        }
        return pyjwt.encode(payload, self._private_key_pem, algorithm="RS256")

    async def _exchange_for_installation_token(self, jwt_token: str) -> str:
        """POST to GitHub to exchange the JWT for an installation access token.

        Args:
            jwt_token: Short-lived RS256 JWT for the App itself.

        Returns:
            The installation access token string.

        Raises:
            httpx.HTTPStatusError: If GitHub returns a non-2xx response.
        """
        url = (
            f"{_GITHUB_API_BASE}/app/installations"
            f"/{self._installation_id}/access_tokens"
        )
        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            return data["token"]
