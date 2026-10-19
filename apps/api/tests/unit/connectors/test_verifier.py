"""CollaboratorPermissionVerifier matrix (C11). Fake GitHub = SIMULATED responses;
these tests prove the mapping, NOT GitHub's real behavior (live verification pending)."""

from __future__ import annotations

import httpx
import pytest

from sourcemind.connectors.github.contracts import AccessVerdict
from sourcemind.connectors.github.verifier import CollaboratorPermissionVerifier
from tests.fakes.fake_github import FakeGitHub, make_client

REPO = "acme/repo"
UID = 101


@pytest.fixture
def fake() -> FakeGitHub:
    f = FakeGitHub()
    f.add_repo(REPO)
    f.user("alice", UID)
    return f


async def _verify(fake: FakeGitHub, *, flag: bool, **kw):
    v = CollaboratorPermissionVerifier(
        make_client(fake), denial_semantics_verified=flag, **kw
    )
    return await v.verify(owner="acme", repo="repo", github_user_id=UID)


@pytest.mark.unit
@pytest.mark.parametrize("flag", [True, False])
@pytest.mark.parametrize("perm", ["read", "write", "admin"])
async def test_explicit_200_with_access_is_authorized(fake, flag, perm):
    fake.permissions["alice"] = perm
    r = await _verify(fake, flag=flag)
    assert (r.verdict, r.reason_code) == (AccessVerdict.AUTHORIZED, "ok")


@pytest.mark.unit
async def test_role_name_read_equivalent_without_permission_field(fake):
    fake.permissions["alice"] = {"role_name": "triage"}
    r = await _verify(fake, flag=False)
    assert r.verdict == AccessVerdict.AUTHORIZED


@pytest.mark.unit
async def test_none_is_denied_only_when_semantics_verified(fake):
    fake.permissions["alice"] = "none"
    denied = await _verify(fake, flag=True)
    assert (denied.verdict, denied.reason_code) == (AccessVerdict.DENIED, "denied")
    unverified = await _verify(fake, flag=False)
    assert unverified.verdict == AccessVerdict.UNVERIFIED
    assert unverified.reason_code == "denial_semantics_unverified"


@pytest.mark.unit
@pytest.mark.parametrize("flag", [True, False])
async def test_404_is_unverified_never_denied(fake, flag):
    r = await _verify(fake, flag=flag)  # no permission entry -> 404
    assert (r.verdict, r.reason_code) == (AccessVerdict.UNVERIFIED, "ambiguous_404")


@pytest.mark.unit
@pytest.mark.parametrize("flag", [True, False])
async def test_403_is_unverified(fake, flag):
    fake.permissions["alice"] = "write"
    fake.inject("/permission", "permission", times=None)
    r = await _verify(fake, flag=flag)
    assert (r.verdict, r.reason_code) == (AccessVerdict.UNVERIFIED, "verifier_forbidden")


@pytest.mark.unit
@pytest.mark.parametrize("flag", [True, False])
@pytest.mark.parametrize("code", ["500", "502"])
async def test_5xx_is_unverified(fake, flag, code):
    fake.permissions["alice"] = "write"
    fake.inject("/permission", code, times=None)
    r = await _verify(fake, flag=flag)
    assert (r.verdict, r.reason_code) == (AccessVerdict.UNVERIFIED, "verifier_unavailable")


@pytest.mark.unit
@pytest.mark.parametrize("kind", ["primary", "secondary"])
async def test_rate_limit_is_unverified(fake, kind):
    fake.permissions["alice"] = "write"
    fake.inject("/permission", kind, times=None)
    # raw path returns the 403 uninterpreted; either reason is UNVERIFIED, never a verdict.
    r = await _verify(fake, flag=True)
    assert r.verdict == AccessVerdict.UNVERIFIED


@pytest.mark.unit
@pytest.mark.parametrize("flag", [True, False])
async def test_timeout_is_unverified(fake, flag):
    class Boom(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.ReadTimeout("slow")

    v = CollaboratorPermissionVerifier(
        _client_with(Boom(), fake),
        denial_semantics_verified=flag,
    )
    r = await v.verify(owner="acme", repo="repo", github_user_id=UID)
    assert r.verdict == AccessVerdict.UNVERIFIED


def _client_with(transport, fake):
    c = make_client(fake)
    c._transport = transport
    return c


@pytest.mark.unit
@pytest.mark.parametrize("flag", [True, False])
@pytest.mark.parametrize(
    "body", [["list"], {"permission": "superuser"}, {"unrelated": 1}, {"permission": 5}]
)
async def test_malformed_200_body_is_unverified(fake, flag, body):
    fake.permissions["alice"] = body
    r = await _verify(fake, flag=flag)
    assert (r.verdict, r.reason_code) == (AccessVerdict.UNVERIFIED, "malformed_response")


@pytest.mark.unit
async def test_non_json_200_is_unverified(fake):
    fake.permissions["alice"] = "write"
    fake.inject("/permission", "bad_json", times=None)
    r = await _verify(fake, flag=True)
    assert r.verdict == AccessVerdict.UNVERIFIED


@pytest.mark.unit
async def test_unknown_user_id_is_unverified(fake):
    v = CollaboratorPermissionVerifier(make_client(fake), denial_semantics_verified=True)
    r = await v.verify(owner="acme", repo="repo", github_user_id=424242)
    assert r.verdict == AccessVerdict.UNVERIFIED


@pytest.mark.unit
async def test_public_repo_exemption_off_by_default(fake):
    fake.repos[REPO].private = False
    r = await _verify(fake, flag=True)  # no permission entry
    assert r.verdict == AccessVerdict.UNVERIFIED
    assert ("GET", f"/repos/{REPO}") not in fake.log


@pytest.mark.unit
async def test_public_repo_exempt_when_enabled(fake):
    fake.repos[REPO].private = False
    r = await _verify(fake, flag=False, public_repos_exempt=True)
    assert (r.verdict, r.reason_code) == (AccessVerdict.AUTHORIZED, "public_repo")


@pytest.mark.unit
async def test_private_repo_not_exempt_even_when_enabled(fake):
    r = await _verify(fake, flag=True, public_repos_exempt=True)
    assert r.verdict == AccessVerdict.UNVERIFIED
