"""
Unit tests for auth/JWT dependency functions.

No database or Clerk network calls — all external I/O is mocked.
Tests cover:
  - _clerk_jwks_url: derivation from publishable key
  - _fetch_jwks: caching and HTTP fetch
  - _verify_clerk_token: RS256 decode, key rotation fallback
  - _fetch_clerk_user_profile: Clerk API parsing + cache + fallback
  - get_current_user: dev-bypass, missing header, bad token, happy path
"""
# ruff: noqa: I001

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import sourcemind.core.dependencies as deps
from sourcemind.core.dependencies import (
    _clerk_jwks_url,
    _fetch_clerk_user_profile,
    _fetch_jwks,
    _verify_clerk_token,
)
from sourcemind.core.exceptions import InternalError, TokenInvalidError, UnauthorizedError


# ── _clerk_jwks_url ──────────────────────────────────────────────────────────

class TestClerkJwksUrl:
    def test_derives_url_from_test_key(self):
        # pk_test_YWxsb3dlZC1tYXlmbHktNTIuY2xlcmsuYWNjb3VudHMuZGV2JA
        # decodes to "allowed-mayfly-52.clerk.accounts.dev$"
        key = "pk_test_YWxsb3dlZC1tYXlmbHktNTIuY2xlcmsuYWNjb3VudHMuZGV2JA"
        url = _clerk_jwks_url(key)
        assert url == "https://allowed-mayfly-52.clerk.accounts.dev/.well-known/jwks.json"

    def test_derives_url_from_live_key(self):
        import base64
        host = "myapp.clerk.accounts.dev"
        b64 = base64.b64encode(f"{host}$".encode()).decode().rstrip("=")
        key = f"pk_live_{b64}"
        url = _clerk_jwks_url(key)
        assert url == f"https://{host}/.well-known/jwks.json"

    # These two previously asserted a fallback to https://api.clerk.com/v1/jwks.
    # That fallback was the bug, not the safety net: the endpoint requires
    # authentication, the JWKS fetch sends none, so it answers 401 forever and
    # every token verification fails. Encoding it here is why an unset
    # CLERK_PUBLISHABLE_KEY reached production and surfaced only as a
    # confusing 401 on every request. The function now fails loudly.

    def test_raises_on_garbage_input(self):
        with pytest.raises(InternalError, match="CLERK_PUBLISHABLE_KEY"):
            _clerk_jwks_url("not_a_valid_key")

    def test_raises_on_empty_string(self):
        with pytest.raises(InternalError, match="CLERK_PUBLISHABLE_KEY"):
            _clerk_jwks_url("")


# ── _fetch_jwks ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestFetchJwks:
    async def test_fetches_and_caches_keys(self):
        deps._jwks_cache.clear()
        mock_keys = [{"kty": "RSA", "kid": "key1"}]

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"keys": mock_keys}

        with patch("sourcemind.core.dependencies.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _fetch_jwks("https://example.com/jwks")

        assert result == mock_keys
        assert "https://example.com/jwks" in deps._jwks_cache

    async def test_returns_cached_keys_within_ttl(self):
        deps._jwks_cache.clear()
        cached_keys = [{"kty": "RSA", "kid": "cached"}]
        deps._jwks_cache["https://cached.example.com/jwks"] = {
            "keys": cached_keys,
            "fetched_at": time.time(),  # fresh
        }

        with patch("sourcemind.core.dependencies.httpx.AsyncClient") as mock_client_cls:
            result = await _fetch_jwks("https://cached.example.com/jwks")
            mock_client_cls.assert_not_called()

        assert result == cached_keys

    async def test_refetches_stale_cache(self):
        deps._jwks_cache.clear()
        deps._jwks_cache["https://stale.example.com/jwks"] = {
            "keys": [{"kid": "old"}],
            "fetched_at": time.time() - 400,  # expired (> 300s TTL)
        }
        fresh_keys = [{"kid": "new"}]

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"keys": fresh_keys}

        with patch("sourcemind.core.dependencies.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _fetch_jwks("https://stale.example.com/jwks")

        assert result == fresh_keys


# ── _verify_clerk_token ───────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestVerifyClerkToken:
    async def test_returns_claims_on_valid_token(self):
        expected_claims = {"sub": "user_abc", "sid": "sess_123"}

        with patch(
            "sourcemind.core.dependencies.jwt.get_unverified_header",
            return_value={"kid": "k1", "alg": "RS256", "typ": "JWT"},
        ), patch(
            "sourcemind.core.dependencies._fetch_jwks",
            return_value=[{"kid": "k1"}],
        ), patch(
            "sourcemind.core.dependencies.jwt.decode", return_value=expected_claims
        ):
            result = await _verify_clerk_token(
                "fake.token.value",
                "https://example.com/jwks",
                issuer="https://example.com",
                authorized_parties=["https://app.example.com"],
            )

        assert result == expected_claims

    async def test_refreshes_jwks_once_for_an_unknown_kid(self):
        good_claims = {"sub": "user_xyz", "sid": "sess_xyz"}

        with patch(
            "sourcemind.core.dependencies.jwt.get_unverified_header",
            return_value={"kid": "good", "alg": "RS256", "typ": "JWT"},
        ), patch(
            "sourcemind.core.dependencies._fetch_jwks",
            side_effect=[[{"kid": "old"}], [{"kid": "good"}]],
        ) as fetch, patch(
            "sourcemind.core.dependencies.jwt.decode", return_value=good_claims
        ):
            result = await _verify_clerk_token(
                "fake.token",
                "https://example.com/jwks",
                issuer="https://example.com",
                authorized_parties=["https://app.example.com"],
            )

        assert result["sub"] == "user_xyz"
        assert fetch.await_count == 2

    async def test_raises_unauthorized_when_all_keys_fail(self):
        from jose import JWTError

        with patch(
            "sourcemind.core.dependencies.jwt.get_unverified_header",
            return_value={"kid": "k1", "alg": "RS256", "typ": "JWT"},
        ), patch(
            "sourcemind.core.dependencies._fetch_jwks",
            return_value=[{"kid": "k1"}],
        ), patch(
            "sourcemind.core.dependencies.jwt.decode", side_effect=JWTError("invalid")
        ):
            with pytest.raises(TokenInvalidError):
                await _verify_clerk_token(
                    "bad.token",
                    "https://example.com/jwks",
                    issuer="https://example.com",
                    authorized_parties=["https://app.example.com"],
                )

    async def test_raises_unauthorized_on_empty_key_list(self):
        with patch(
            "sourcemind.core.dependencies.jwt.get_unverified_header",
            return_value={"kid": "k1", "alg": "RS256", "typ": "JWT"},
        ), patch("sourcemind.core.dependencies._fetch_jwks", return_value=[]):
            with pytest.raises(TokenInvalidError):
                await _verify_clerk_token(
                    "any.token",
                    "https://example.com/jwks",
                    issuer="https://example.com",
                    authorized_parties=["https://app.example.com"],
                )


# ── _fetch_clerk_user_profile ─────────────────────────────────────────────────

@pytest.mark.asyncio
class TestFetchClerkUserProfile:
    def setup_method(self):
        deps._user_profile_cache.clear()

    async def test_returns_cached_profile(self):
        deps._user_profile_cache["user_cached"] = ("cached@example.com", "Cached User")

        with patch("sourcemind.core.dependencies.httpx.AsyncClient") as mock_cls:
            email, name = await _fetch_clerk_user_profile("user_cached", "sk_test_xxx")
            mock_cls.assert_not_called()

        assert email == "cached@example.com"
        assert name == "Cached User"

    async def test_fetches_and_parses_clerk_api_response(self):
        clerk_response = {
            "primary_email_address_id": "ea_001",
            "email_addresses": [{"id": "ea_001", "email_address": "alice@example.com"}],
            "first_name": "Alice",
            "last_name": "Smith",
        }
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = clerk_response

        with patch("sourcemind.core.dependencies.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            email, name = await _fetch_clerk_user_profile("user_alice", "sk_test_xxx")

        assert email == "alice@example.com"
        assert name == "Alice Smith"
        assert deps._user_profile_cache["user_alice"] == ("alice@example.com", "Alice Smith")

    async def test_fails_closed_on_api_error(self):
        with patch("sourcemind.core.dependencies.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=Exception("network error"))
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(UnauthorizedError):
                await _fetch_clerk_user_profile("user_fail", "sk_test_xxx")

    async def test_display_name_none_when_no_name_fields(self):
        clerk_response = {
            "primary_email_address_id": "ea_002",
            "email_addresses": [{"id": "ea_002", "email_address": "noname@example.com"}],
            "first_name": None,
            "last_name": None,
        }
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = clerk_response

        with patch("sourcemind.core.dependencies.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            email, name = await _fetch_clerk_user_profile("user_noname", "sk_test_xxx")

        assert email == "noname@example.com"
        assert name is None


