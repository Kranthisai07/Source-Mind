"""Unit tests for GitHubAppAuth — JWT generation and token caching."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.unit
def test_generate_jwt_structure():
    """Generated JWT should be a valid RS256 token with correct claims."""
    import jwt as pyjwt

    from sourcemind.connectors.github.app_auth import GitHubAppAuth

    # Generate a throwaway RSA key pair for testing
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.backends import default_backend

    private_key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend()
    )
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

    redis_mock = MagicMock()
    auth = GitHubAppAuth(
        app_id="12345",
        private_key_pem=pem,
        installation_id="99999",
        redis_client=redis_mock,
    )

    token = auth._generate_jwt()
    # Decode without verification to inspect claims
    public_key = private_key.public_key()
    payload = pyjwt.decode(token, public_key, algorithms=["RS256"])

    assert payload["iss"] == "12345"
    assert payload["exp"] > time.time()
    assert payload["iat"] < time.time()


@pytest.mark.unit
@pytest.mark.anyio
async def test_get_token_uses_cache():
    """get_token() should return cached value without calling GitHub."""
    from sourcemind.connectors.github.app_auth import GitHubAppAuth

    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(return_value="cached_token_123")

    auth = GitHubAppAuth(
        app_id="1",
        private_key_pem="pem",
        installation_id="2",
        redis_client=redis_mock,
    )
    token = await auth.get_token()
    assert token == "cached_token_123"
    redis_mock.get.assert_called_once()


@pytest.mark.unit
@pytest.mark.anyio
async def test_get_token_fetches_when_cache_miss():
    """get_token() should call GitHub when cache is empty."""
    from sourcemind.connectors.github.app_auth import GitHubAppAuth

    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(return_value=None)
    redis_mock.setex = AsyncMock()

    auth = GitHubAppAuth(
        app_id="1",
        private_key_pem="pem",
        installation_id="2",
        redis_client=redis_mock,
    )
    auth._generate_jwt = MagicMock(return_value="jwt_token")
    auth._exchange_for_installation_token = AsyncMock(return_value="new_token_abc")

    token = await auth.get_token()
    assert token == "new_token_abc"
    redis_mock.setex.assert_called_once()
    call_args = redis_mock.setex.call_args
    assert call_args[0][2] == "new_token_abc"
    # TTL should be 50 minutes
    assert call_args[0][1] == 50 * 60


@pytest.mark.unit
@pytest.mark.anyio
async def test_invalidate_cache_deletes_key():
    """invalidate_cache() should call redis.delete on the cache key."""
    from sourcemind.connectors.github.app_auth import GitHubAppAuth

    redis_mock = AsyncMock()
    redis_mock.delete = AsyncMock()

    auth = GitHubAppAuth(
        app_id="1",
        private_key_pem="pem",
        installation_id="42",
        redis_client=redis_mock,
    )
    await auth.invalidate_cache()
    redis_mock.delete.assert_called_once_with("gh_install_token:42")
