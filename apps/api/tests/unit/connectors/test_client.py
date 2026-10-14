"""Tests for connectors/github/client.py: typed errors, pagination, conditional GET.

All HTTP is the in-memory fake (SIMULATED GitHub); no network.
"""

from __future__ import annotations

import httpx
import pytest

from sourcemind.connectors.github.client import MAX_INLINE_SLEEP_S, NOT_MODIFIED, GitHubClient
from sourcemind.connectors.github.contracts import (
    GoneError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitedError,
    TransientError,
)
from tests.fakes.fake_github import FakeGitHub, make_client

REPO = "acme/repo"


class _AsyncFn(httpx.AsyncBaseTransport):
    def __init__(self, fn):
        self._fn = fn

    async def handle_async_request(self, request):
        return await self._fn(request)


@pytest.fixture
def fake() -> FakeGitHub:
    f = FakeGitHub()
    f.add_repo(REPO)
    return f


# ─── Link header parsing ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_next_link_is_extracted_from_a_multi_rel_header():
    header = (
        '<https://api.github.com/repos/o/r/commits?page=2>; rel="next", '
        '<https://api.github.com/repos/o/r/commits?page=9>; rel="last"'
    )
    assert GitHubClient._extract_next_link(header) == (
        "https://api.github.com/repos/o/r/commits?page=2"
    )


@pytest.mark.unit
@pytest.mark.parametrize("header", ["", "garbage", "<no-rel-here>", '<x>; rel="prev"'])
def test_no_next_link_terminates(header):
    assert GitHubClient._extract_next_link(header) is None


# ─── pagination ──────────────────────────────────────────────────────────────


@pytest.mark.unit
async def test_pagination_follows_link_and_is_exhausted(fake):
    for i in range(5):
        fake.add_issue(REPO, "a", f"t{i}")
    pager = make_client(fake).list_issues("acme", "repo", per_page=2)
    items = [i async for i in pager]
    assert len(items) == 5
    assert pager.exhausted is True
    assert pager.pages_fetched == 3


@pytest.mark.unit
async def test_page_cap_sets_exhausted_false_not_silent_end(fake):
    for i in range(10):
        fake.add_issue(REPO, "a", f"t{i}")
    pager = make_client(fake).list_issues("acme", "repo", per_page=2, max_pages=2)
    items = [i async for i in pager]
    assert len(items) == 4
    assert pager.exhausted is False
    assert len(fake.log) == 2


@pytest.mark.unit
async def test_empty_listing_is_exhausted_true(fake):
    pager = make_client(fake).list_issues("acme", "repo")
    assert [i async for i in pager] == []
    assert pager.exhausted is True


@pytest.mark.unit
async def test_installation_repositories_unwraps_items(fake):
    pager = make_client(fake).list_installation_repositories()
    names = [r["full_name"] async for r in pager]
    assert names == [REPO]


@pytest.mark.unit
async def test_compare_merges_pages_and_reports_cap(fake):
    base = fake.add_commit(REPO, "base", "a")
    for i in range(5):
        fake.add_commit(REPO, f"c{i}", "a")
    head = fake._repo(REPO).head
    full = await make_client(fake).compare("acme", "repo", base, head, per_page=2)
    assert full.status == "ahead" and len(full.commits) == 5 and full.exhausted
    capped = await make_client(fake).compare("acme", "repo", base, head, per_page=2, max_pages=2)
    assert len(capped.commits) == 4 and capped.exhausted is False


# ─── error classification ────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    ("kind", "exc"),
    [
        ("primary", RateLimitedError),
        ("secondary", RateLimitedError),
        ("permission", PermissionDeniedError),
        ("404", NotFoundError),
        ("410", GoneError),
        ("500", TransientError),
        ("502", TransientError),
        ("bad_json", TransientError),
    ],
)
async def test_errors_are_classified(fake, kind, exc):
    fake.inject("/issues", kind, times=None, retry_after=3600)
    with pytest.raises(exc):
        _ = [i async for i in make_client(fake).list_issues("acme", "repo")]


@pytest.mark.unit
async def test_primary_rate_limit_carries_reset_time(fake):
    fake.inject("/issues", "primary", times=None)
    with pytest.raises(RateLimitedError) as info:
        _ = [i async for i in make_client(fake).list_issues("acme", "repo")]
    assert info.value.reset_at is not None
    assert info.value.retry_after_s is not None


@pytest.mark.unit
async def test_persistent_403_does_not_loop_to_page_cap(fake):
    """Regression: the old code slept and looped to the 50-page cap on a 403."""
    sleeps: list[float] = []
    fake.inject("/issues", "permission", times=None)
    with pytest.raises(PermissionDeniedError):
        _ = [i async for i in make_client(fake, sleeps=sleeps).list_issues("acme", "repo")]
    assert len(fake.log) == 1
    assert sleeps == []


@pytest.mark.unit
async def test_persistent_rate_limit_is_bounded(fake):
    sleeps: list[float] = []
    fake.inject("/issues", "secondary", times=None, retry_after=5)
    with pytest.raises(RateLimitedError):
        _ = [i async for i in make_client(fake, sleeps=sleeps).list_issues("acme", "repo")]
    assert len(fake.log) == 2  # one request, one inline retry, then raise
    assert sleeps == [5.0]


@pytest.mark.unit
async def test_short_retry_after_sleeps_once_then_succeeds(fake):
    sleeps: list[float] = []
    fake.add_issue(REPO, "a", "t")
    fake.inject("/issues", "secondary", times=1, retry_after=7)
    items = [i async for i in make_client(fake, sleeps=sleeps).list_issues("acme", "repo")]
    assert len(items) == 1
    assert sleeps == [7.0]


@pytest.mark.unit
async def test_long_retry_after_raises_without_sleeping(fake):
    sleeps: list[float] = []
    fake.inject("/issues", "secondary", times=None, retry_after=int(MAX_INLINE_SLEEP_S) + 1)
    with pytest.raises(RateLimitedError) as info:
        _ = [i async for i in make_client(fake, sleeps=sleeps).list_issues("acme", "repo")]
    assert sleeps == []
    assert info.value.retry_after_s == MAX_INLINE_SLEEP_S + 1


@pytest.mark.unit
async def test_401_invalidates_token_and_retries_once(fake):
    fake.add_issue(REPO, "a", "t")
    calls = {"n": 0}
    real = fake.transport

    async def flaky(request: httpx.Request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(401, json={"message": "Bad credentials"})
        return await real.handle_async_request(request)

    client = make_client(fake)
    client._transport = _AsyncFn(flaky)
    items = [i async for i in client.list_issues("acme", "repo")]
    assert len(items) == 1
    client.auth_stub.invalidate_cache.assert_awaited_once()
    assert calls["n"] == 2


@pytest.mark.unit
async def test_second_401_is_permission_denied(fake):
    async def always_401(request: httpx.Request):
        return httpx.Response(401, json={"message": "Bad credentials"})

    client = make_client(fake)
    client._transport = _AsyncFn(always_401)
    with pytest.raises(PermissionDeniedError):
        _ = [i async for i in client.list_issues("acme", "repo")]
    client.auth_stub.invalidate_cache.assert_awaited_once()


@pytest.mark.unit
async def test_network_failure_is_transient(fake):
    async def boom(request: httpx.Request):
        raise httpx.ConnectTimeout("t")

    client = make_client(fake)
    client._transport = _AsyncFn(boom)
    with pytest.raises(TransientError):
        await client.get_repo("acme", "repo")


# ─── conditional GET ─────────────────────────────────────────────────────────


@pytest.mark.unit
async def test_conditional_get_returns_not_modified_sentinel(fake):
    n = fake.add_pull(REPO, "a", "p")
    fake.add_review(REPO, n, "r", "looks good")
    client = make_client(fake)
    path = f"/repos/{REPO}/pulls/{n}/reviews"
    first = await client.conditional_get(path, None)
    assert first.etag
    again = await client.conditional_get(path, first.etag)
    assert again is NOT_MODIFIED
    fake.add_review(REPO, n, "r2", "more")
    changed = await client.conditional_get(path, first.etag)
    assert changed is not NOT_MODIFIED and len(changed.body) == 2


@pytest.mark.unit
async def test_paginator_not_modified_with_etag(fake):
    n = fake.add_pull(REPO, "a", "p")
    fake.add_review(REPO, n, "r", "ok")
    client = make_client(fake)
    p1 = client.list_pull_reviews("acme", "repo", n)
    assert len([r async for r in p1]) == 1 and p1.etag
    p2 = client.list_pull_reviews("acme", "repo", n, etag=p1.etag)
    assert [r async for r in p2] == [] and p2.not_modified


# ─── collaborator permission: raw, uninterpreted ─────────────────────────────


@pytest.mark.unit
async def test_collaborator_permission_returns_status_and_body_without_raising(fake):
    fake.permissions["alice"] = "write"
    client = make_client(fake)
    ok = await client.collaborator_permission("acme", "repo", "alice")
    assert ok.status == 200 and ok.body["permission"] == "write"
    missing = await client.collaborator_permission("acme", "repo", "nobody")
    assert missing.status == 404
    fake.inject("/permission", "permission", times=1)
    denied = await client.collaborator_permission("acme", "repo", "alice")
    assert denied.status == 403
