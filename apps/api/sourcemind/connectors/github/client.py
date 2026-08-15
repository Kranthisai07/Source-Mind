"""GitHub REST + GraphQL API client with pagination and rate-limit handling."""

from __future__ import annotations

import asyncio
import re
from typing import Any, AsyncIterator

import httpx
import structlog

from sourcemind.connectors.github.app_auth import GitHubAppAuth

log = structlog.get_logger(__name__)

_API_BASE = "https://api.github.com"
_GRAPHQL_ENDPOINT = "https://api.github.com/graphql"
_DEFAULT_PER_PAGE = 100
_RATE_LIMIT_BACKOFF = 60  # seconds to wait when secondary rate limit hit


class GitHubClient:
    """Async GitHub client authenticated via a GitHub App installation token.

    All REST list calls are automatically paginated via the ``Link`` header.
    GraphQL calls are used for Discussions (not available in REST v3).

    Args:
        auth: Configured :class:`GitHubAppAuth` instance.
    """

    def __init__(self, auth: GitHubAppAuth) -> None:
        self._auth = auth

    # ── Commits ───────────────────────────────────────────────────────────────

    async def get_commits(
        self,
        owner: str,
        repo: str,
        since: str | None = None,
        per_page: int = _DEFAULT_PER_PAGE,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield all commits for *owner/repo*, optionally filtered by *since* (ISO 8601).

        Args:
            owner: Repository owner login.
            repo: Repository name.
            since: If given, only commits after this timestamp are returned.
            per_page: Page size (max 100).
        """
        params: dict[str, Any] = {"per_page": per_page}
        if since:
            params["since"] = since
        async for item in self._paginate(
            f"{_API_BASE}/repos/{owner}/{repo}/commits", params
        ):
            yield item

    # ── Pull Requests ─────────────────────────────────────────────────────────

    async def get_pull_requests(
        self,
        owner: str,
        repo: str,
        state: str = "all",
        since: str | None = None,
        per_page: int = _DEFAULT_PER_PAGE,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield pull requests for *owner/repo*.

        Args:
            owner: Repository owner login.
            repo: Repository name.
            state: ``"open"``, ``"closed"``, or ``"all"``.
            since: If given, filter PRs updated after this timestamp.
            per_page: Page size (max 100).
        """
        params: dict[str, Any] = {"state": state, "per_page": per_page}
        if since:
            # Sort by updated desc only when filtering — avoids expensive full-sort
            # on large repos (e.g. django/django with 14k+ PRs) when no filter needed.
            params["sort"] = "updated"
            params["direction"] = "desc"
        async for item in self._paginate(
            f"{_API_BASE}/repos/{owner}/{repo}/pulls", params
        ):
            # Apply since filter client-side (REST /pulls has no `since` param)
            if since and item.get("updated_at", "") < since:
                return
            yield item

    # ── Issues ────────────────────────────────────────────────────────────────

    async def get_issues(
        self,
        owner: str,
        repo: str,
        state: str = "closed",
        since: str | None = None,
        per_page: int = _DEFAULT_PER_PAGE,
        max_pages: int = 10,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield issues for *owner/repo* (excludes pull requests).

        Args:
            owner: Repository owner login.
            repo: Repository name.
            state: ``"open"``, ``"closed"`` (default), or ``"all"``.
                   Defaulting to ``"closed"`` avoids paging through thousands
                   of open PRs on repos like django/django.
            since: ISO 8601 timestamp; only issues updated after this date.
            per_page: Page size (max 100).
            max_pages: Hard cap on pages to fetch (default 10).
        """
        params: dict[str, Any] = {"state": state, "per_page": per_page}
        if since:
            params["since"] = since
        async for item in self._paginate(
            f"{_API_BASE}/repos/{owner}/{repo}/issues", params, max_pages=max_pages
        ):
            # Skip pull requests (GitHub issues endpoint includes both)
            if "pull_request" not in item:
                yield item

    # ── PR enrichment endpoints ───────────────────────────────────────────────

    async def get_pr_comments(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        per_page: int = _DEFAULT_PER_PAGE,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield issue-thread comments on a pull request.

        Uses the Issues comments endpoint (``/issues/{number}/comments``), which
        returns the top-level conversation comments — distinct from inline review
        comments returned by :meth:`get_pr_review_comments`.

        Args:
            owner: Repository owner login.
            repo: Repository name.
            pr_number: Pull request number.
            per_page: Page size (max 100).
        """
        async for item in self._paginate(
            f"{_API_BASE}/repos/{owner}/{repo}/issues/{pr_number}/comments",
            {"per_page": per_page},
        ):
            yield item

    async def get_pr_reviews(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        per_page: int = _DEFAULT_PER_PAGE,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield review objects (APPROVED, CHANGES_REQUESTED, COMMENTED, etc.).

        Args:
            owner: Repository owner login.
            repo: Repository name.
            pr_number: Pull request number.
            per_page: Page size (max 100).
        """
        async for item in self._paginate(
            f"{_API_BASE}/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
            {"per_page": per_page},
        ):
            yield item

    async def get_pr_review_comments(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        per_page: int = _DEFAULT_PER_PAGE,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield inline diff/review comments on a pull request.

        These are the line-level comments attached to specific code hunks,
        distinct from top-level issue comments returned by :meth:`get_pr_comments`.

        Args:
            owner: Repository owner login.
            repo: Repository name.
            pr_number: Pull request number.
            per_page: Page size (max 100).
        """
        async for item in self._paginate(
            f"{_API_BASE}/repos/{owner}/{repo}/pulls/{pr_number}/comments",
            {"per_page": per_page},
        ):
            yield item

    # ── Discussions (GraphQL) ─────────────────────────────────────────────────

    async def get_discussions(
        self,
        owner: str,
        repo: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return up to *limit* discussions for *owner/repo* via GraphQL.

        Discussions are not available in the GitHub REST API v3.

        Args:
            owner: Repository owner login.
            repo: Repository name.
            limit: Maximum number of discussions to retrieve (max 100 per call).
        """
        query = """
        query($owner: String!, $repo: String!, $first: Int!, $after: String) {
          repository(owner: $owner, name: $repo) {
            discussions(first: $first, after: $after) {
              pageInfo { hasNextPage endCursor }
              nodes {
                number
                title
                body
                createdAt
                updatedAt
                url
                author { login }
                category { name }
                comments(first: 10) {
                  nodes {
                    body
                    author { login }
                  }
                }
              }
            }
          }
        }
        """
        results: list[dict[str, Any]] = []
        cursor: str | None = None
        batch_size = min(limit, 100)

        while True:
            variables: dict[str, Any] = {
                "owner": owner,
                "repo": repo,
                "first": batch_size,
            }
            if cursor:
                variables["after"] = cursor

            data = await self._graphql(query, variables)
            discussions_data = (
                data.get("data", {})
                .get("repository", {})
                .get("discussions", {})
            )
            nodes = discussions_data.get("nodes", [])
            results.extend(nodes)

            page_info = discussions_data.get("pageInfo", {})
            if not page_info.get("hasNextPage") or len(results) >= limit:
                break
            cursor = page_info["endCursor"]
            batch_size = min(limit - len(results), 100)

        return results[:limit]

    # ── Rate-limit aware request helpers ──────────────────────────────────────

    async def _get_headers(self) -> dict[str, str]:
        """Build authorization headers using a fresh (or cached) token."""
        token = await self._auth.get_token()
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def _paginate(
        self,
        url: str,
        params: dict[str, Any],
        max_pages: int = 50,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield individual items from a paginated REST endpoint.

        Follows the ``Link: <url>; rel="next"`` header automatically.
        Backs off and retries on 403/429 secondary rate limit responses.

        Args:
            url: Initial REST URL to fetch.
            params: Query parameters for the first request.
            max_pages: Hard cap on pages fetched (default 50) to prevent
                       runaway pagination on repos with no matching items.
        """
        next_url: str | None = url
        current_params: dict[str, Any] | None = params
        pages_fetched = 0
        # Use a short connect timeout + generous read timeout.
        # A fresh client per page avoids keeping a long-lived connection open
        # across generator yields, which prevents asyncio cleanup hangs when
        # the caller breaks early (GeneratorExit can't interrupt an in-flight await).
        _timeout = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)

        while next_url and pages_fetched < max_pages:
            pages_fetched += 1
            headers = await self._get_headers()
            async with httpx.AsyncClient(timeout=_timeout) as client:
                response = await client.get(
                    next_url,
                    headers=headers,
                    params=current_params,
                )
            current_params = None  # params only on first request

            if response.status_code in (403, 429):
                retry_after = int(response.headers.get("Retry-After", _RATE_LIMIT_BACKOFF))
                log.warning(
                    "github_rate_limit_hit",
                    status=response.status_code,
                    retry_after=retry_after,
                    url=next_url,
                )
                await asyncio.sleep(retry_after)
                continue

            if response.status_code == 401:
                # Token might have expired mid-session — invalidate and retry once
                await self._auth.invalidate_cache()
                headers = await self._get_headers()
                async with httpx.AsyncClient(timeout=_timeout) as client:
                    response = await client.get(next_url, headers=headers)

            response.raise_for_status()

            items = response.json()
            if isinstance(items, list):
                for item in items:
                    yield item
            else:
                yield items

            next_url = self._extract_next_link(response.headers.get("Link", ""))

    async def _graphql(
        self,
        query: str,
        variables: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a GraphQL query against the GitHub API.

        Args:
            query: GraphQL query string.
            variables: Variables dict for the query.

        Returns:
            Parsed JSON response dict.

        Raises:
            httpx.HTTPStatusError: On non-2xx HTTP response.
            ValueError: If the GraphQL response contains errors.
        """
        headers = await self._get_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                _GRAPHQL_ENDPOINT,
                headers=headers,
                json={"query": query, "variables": variables},
            )
            response.raise_for_status()
            data = response.json()

        if "errors" in data:
            log.error("github_graphql_errors", errors=data["errors"])
            raise ValueError(f"GitHub GraphQL errors: {data['errors']}")
        return data

    # ── Utilities ─────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_next_link(link_header: str) -> str | None:
        """Parse the ``Link`` header and return the ``next`` URL if present.

        Args:
            link_header: Raw ``Link`` header value from the response.

        Returns:
            The URL for the next page, or ``None`` if there is no next page.
        """
        if not link_header:
            return None
        match = re.search(r'<([^>]+)>;\s*rel="next"', link_header)
        return match.group(1) if match else None
