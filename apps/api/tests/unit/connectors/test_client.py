"""Tests for connectors/github/client.py pagination.

The audit flagged a real hazard here: on a 403/429 the loop sleeps and
`continue`s without rolling back `pages_fetched` and without a retry counter,
so a persistently-403 URL burns all 50 page slots sleeping 60s each — roughly
50 minutes of a worker doing nothing. These tests pin the page cap and the
Link-header parsing that bound that loop.

httpx is mocked at the client boundary; there is no network here.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sourcemind.connectors.github.client import GitHubClient


def _auth():
    a = MagicMock()
    a.get_token = AsyncMock(return_value="ghs_faketoken")
    a.invalidate_cache = AsyncMock()
    return a


def _response(status=200, json_data=None, link=""):
    r = MagicMock()
    r.status_code = status
    r.json = MagicMock(return_value=json_data if json_data is not None else [])
    r.headers = {"Link": link} if link else {}
    return r


def _patched_client(responses: list):
    """Patch httpx.AsyncClient so each request returns the next response."""
    calls = {"n": 0}

    async def get(*args, **kwargs):
        i = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        return responses[i]

    client = AsyncMock()
    client.get = AsyncMock(side_effect=get)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client, calls


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
def test_no_next_link_on_the_final_page():
    header = '<https://api.github.com/repos/o/r/commits?page=1>; rel="prev"'
    assert GitHubClient._extract_next_link(header) is None


@pytest.mark.unit
@pytest.mark.parametrize("header", ["", "garbage", "<no-rel-here>"])
def test_malformed_link_headers_terminate_pagination(header):
    """Returning something truthy for a malformed header would loop forever."""
    assert GitHubClient._extract_next_link(header) is None


# ─── page cap ────────────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_pagination_stops_at_the_page_cap():
    """A repo whose Link header always advertises a next page must not loop
    indefinitely — the cap is the only thing bounding it."""
    always_more = _response(
        200,
        [{"sha": "abc"}],
        link='<https://api.github.com/next>; rel="next"',
    )
    client, calls = _patched_client([always_more])

    with patch("httpx.AsyncClient", return_value=client):
        items = [
            item
            async for item in GitHubClient(_auth())._paginate(
                "https://api.github.com/start", {}, max_pages=3
            )
        ]

    assert calls["n"] == 3, f"expected exactly 3 page fetches, got {calls['n']}"
    assert len(items) == 3


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pagination_stops_when_no_next_link_is_returned():
    single = _response(200, [{"sha": "a"}, {"sha": "b"}], link="")
    client, calls = _patched_client([single])

    with patch("httpx.AsyncClient", return_value=client):
        items = [
            item
            async for item in GitHubClient(_auth())._paginate(
                "https://api.github.com/start", {}, max_pages=50
            )
        ]

    assert calls["n"] == 1, "a page without a next link must end pagination"
    assert len(items) == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_each_item_in_a_page_is_yielded_individually():
    page = _response(200, [{"sha": str(i)} for i in range(5)])
    client, _ = _patched_client([page])

    with patch("httpx.AsyncClient", return_value=client):
        items = [
            item
            async for item in GitHubClient(_auth())._paginate(
                "https://api.github.com/start", {}
            )
        ]

    assert [i["sha"] for i in items] == ["0", "1", "2", "3", "4"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_an_empty_page_yields_nothing_and_terminates():
    client, calls = _patched_client([_response(200, [])])

    with patch("httpx.AsyncClient", return_value=client):
        items = [
            item
            async for item in GitHubClient(_auth())._paginate(
                "https://api.github.com/start", {}
            )
        ]

    assert items == []
    assert calls["n"] == 1


# ─── rate limiting ───────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_rate_limited_response_is_retried_but_still_bounded():
    """A persistent 403 must terminate rather than spin forever.

    The loop sleeps and continues without rolling back pages_fetched, so the
    page cap is what stops it. Sleep is patched out, otherwise this test would
    take the full backoff.
    """
    rate_limited = _response(403)
    rate_limited.headers = {"Retry-After": "1"}
    client, calls = _patched_client([rate_limited])

    with (
        patch("httpx.AsyncClient", return_value=client),
        patch("asyncio.sleep", new=AsyncMock()) as slept,
    ):
        items = [
            item
            async for item in GitHubClient(_auth())._paginate(
                "https://api.github.com/start", {}, max_pages=3
            )
        ]

    assert items == []
    assert calls["n"] <= 3, "a persistent 403 must be bounded by the page cap"
    assert slept.await_count >= 1, "the backoff should have been applied"
