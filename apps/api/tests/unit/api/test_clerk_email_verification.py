"""The trust-critical Clerk parser, exercised with only the HTTP layer mocked.

Member-add tests replace fetch_clerk_primary_email_verification wholesale; these
call the REAL function so the primary-entry lookup and the verification-status
read are covered directly.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from sourcemind.core import dependencies
from sourcemind.core.exceptions import ServiceUnavailableError

_FETCH = "sourcemind.core.dependencies._fetch_clerk_user_json"


def _user(
    *, primary="idn_1", address="friend@example.com", verification: object = ("verified",)
) -> dict:
    entry: dict = {"id": "idn_1", "email_address": address}
    if verification != "absent":
        entry["verification"] = (
            None if verification is None else {"status": verification[0]}  # type: ignore[index]
        )
    return {"primary_email_address_id": primary, "email_addresses": [entry]}


async def _verify(data=None, *, error: Exception | None = None):
    mock = AsyncMock(return_value=data) if error is None else AsyncMock(side_effect=error)
    with patch(_FETCH, new=mock):
        return await dependencies.fetch_clerk_primary_email_verification("user_x", "sk_test")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verified_primary_email_is_returned_with_its_status() -> None:
    assert await _verify(_user()) == ("friend@example.com", "verified")


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["unverified", "expired", "failed", "transferable"])
async def test_non_verified_statuses_are_passed_through_for_the_caller_to_refuse(
    status,
) -> None:
    assert await _verify(_user(verification=(status,))) == ("friend@example.com", status)


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("verification", ["absent", None], ids=["key-absent", "null"])
async def test_missing_or_null_verification_block_yields_no_status(verification) -> None:
    assert await _verify(_user(verification=verification)) == ("friend@example.com", None)


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("primary", ["idn_other", None, ""])
async def test_primary_id_matching_no_entry_yields_no_email(primary) -> None:
    assert await _verify(_user(primary=primary)) == ("", None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_primary_id_never_matches_an_entry_without_an_id() -> None:
    data = {
        "primary_email_address_id": None,
        "email_addresses": [
            {"email_address": "x@example.com", "verification": {"status": "verified"}}
        ],
    }
    assert await _verify(data) == ("", None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transport_error_fails_closed_with_503() -> None:
    with pytest.raises(ServiceUnavailableError):
        await _verify(error=httpx.ConnectTimeout("slow"))


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body", [[], "oops", {"primary_email_address_id": "idn_1", "email_addresses": "x"}]
)
async def test_malformed_body_fails_closed_with_503(body) -> None:
    with pytest.raises(ServiceUnavailableError):
        await _verify(body)


def _status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.clerk.com/v1/users/user_x")
    return httpx.HTTPStatusError(
        "x", request=request, response=httpx.Response(code, request=request)
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_clerk_404_means_not_verified_not_an_outage() -> None:
    assert await _verify(error=_status_error(404)) == ("", None)


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("code", [401, 429, 500, 503])
async def test_other_http_errors_fail_closed_with_503(code) -> None:
    with pytest.raises(ServiceUnavailableError):
        await _verify(error=_status_error(code))
