"""GitHub REST + GraphQL API client with typed errors and bounded pagination.

Every non-2xx response is classified into a :mod:`contracts` exception
(``RateLimitedError`` / ``PermissionDeniedError`` / ``NotFoundError`` /
``GoneError`` / ``TransientError``); nothing is ever returned as an empty
result for an error, and a persistent 403 can never loop to the page cap.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from sourcemind.connectors.github.app_auth import GitHubAppAuth
from sourcemind.connectors.github.contracts import (
    GitHubFetchError,
    GoneError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitedError,
    TransientError,
)

log = structlog.get_logger(__name__)

_API_BASE = "https://api.github.com"
_GRAPHQL_ENDPOINT = "https://api.github.com/graphql"
_DEFAULT_PER_PAGE = 100
DEFAULT_MAX_PAGES = 50
#: A single in-request sleep is allowed only for an explicit ``Retry-After`` at or
#: below this many seconds. Anything longer raises ``RateLimitedError``.
MAX_INLINE_SLEEP_S = 30.0
_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)


class NotModified:
    """Sentinel: a conditional GET (``If-None-Match``) answered 304."""

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return "NOT_MODIFIED"


NOT_MODIFIED = NotModified()


@dataclass(frozen=True)
class RawResponse:
    """Status plus parsed JSON body, uninterpreted (``body`` is None if not JSON)."""

    status: int
    body: Any
    etag: str | None = None


@dataclass
class CompareResult:
    """``compare base...head`` with all (page-capped) commits, oldest first."""

    status: str  # 'ahead' | 'behind' | 'identical' | 'diverged'
    ahead_by: int = 0
    behind_by: int = 0
    commits: list[dict[str, Any]] = field(default_factory=list)
    exhausted: bool = True  # False when the page cap stopped the commit listing


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise TransientError("malformed GitHub response (not JSON)") from exc


def _message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    return str(body.get("message", "")) if isinstance(body, dict) else ""


class Paginator:
    """Async-iterable over the items of a ``Link``-paginated endpoint.

    After iteration ends, ``exhausted`` is ``True`` only if the listing ran to its
    last page. ``False`` means the page cap stopped it (the inventory is incomplete).
    ``None`` means iteration has not finished (e.g. the consumer broke out early).
    ``etag`` is the first page's ETag, set only when the whole listing fits in one
    page (a first-page ETag cannot vouch for later pages). ``not_modified`` is set
    when a conditional first request answered 304.
    """

    def __init__(
        self,
        client: GitHubClient,
        url: str,
        params: dict[str, Any] | None,
        *,
        max_pages: int,
        items_key: str | None = None,
        etag: str | None = None,
    ) -> None:
        self._client = client
        self._url = url
        self._params = params
        self._max_pages = max_pages
        self._items_key = items_key
        self._if_none_match = etag
        self.exhausted: bool | None = None
        self.not_modified = False
        self.etag: str | None = None
        self.pages_fetched = 0

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[dict[str, Any]]:
        next_url: str | None = self._url
        params = self._params
        while next_url:
            if self.pages_fetched >= self._max_pages:
                self.exhausted = False
                return
            first = self.pages_fetched == 0
            response = await self._client._send(
                next_url, params, etag=self._if_none_match if first else None
            )
            self.pages_fetched += 1
            params = None  # only the first request carries params; Link URLs embed them
            if response.status_code == 304:
                self.not_modified = True
                self.exhausted = True
                return
            data = _json(response)
            if self._items_key is not None:
                items = data.get(self._items_key) if isinstance(data, dict) else None
                if not isinstance(items, list):
                    raise TransientError("malformed GitHub response (no item list)")
            else:
                items = data if isinstance(data, list) else [data]
            next_url = GitHubClient._extract_next_link(response.headers.get("Link", ""))
            if first and next_url is None:
                self.etag = response.headers.get("ETag")
            for item in items:
                yield item
        self.exhausted = True


class GitHubClient:
    """Async GitHub client authenticated via a GitHub App installation token.

    REST list calls return a :class:`Paginator` (``Link`` header, configurable page
    cap, ``exhausted`` flag). GraphQL Discussions remain for legacy callers only; the
    automatic-ingestion path never calls them.

    Args:
        auth: Configured :class:`GitHubAppAuth` (anything with ``get_token`` and
            ``invalidate_cache``).
        transport: Optional httpx transport (tests inject ``httpx.MockTransport``).
        max_pages: Default page cap for paginated listings.
        sleep: Awaitable sleep (injectable in tests).
        base_url: REST API base URL.
    """

    def __init__(
        self,
        auth: GitHubAppAuth,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        max_pages: int = DEFAULT_MAX_PAGES,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        base_url: str = _API_BASE,
    ) -> None:
        self._auth = auth
        self._transport = transport
        self._max_pages = max_pages
        self._sleep = sleep or asyncio.sleep
        self._base = base_url.rstrip("/")

    # ── Typed, bounded discovery methods ─────────────────────────────────────

    def _paginator(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        max_pages: int | None = None,
        items_key: str | None = None,
        etag: str | None = None,
    ) -> Paginator:
        return Paginator(
            self,
            f"{self._base}{path}",
            params,
            max_pages=max_pages or self._max_pages,
            items_key=items_key,
            etag=etag,
        )

    async def get_repo(self, owner: str, repo: str) -> dict[str, Any]:
        """``GET /repos/{owner}/{repo}`` (``private``, ``default_branch``)."""
        response = await self._send(f"{self._base}/repos/{owner}/{repo}", None)
        data = _json(response)
        if not isinstance(data, dict):
            raise TransientError("malformed GitHub response (repo)")
        return data

    async def get_head_sha(self, owner: str, repo: str, branch: str | None = None) -> str:
        """Head commit SHA of *branch* (the default branch when omitted)."""
        if branch is None:
            branch = (await self.get_repo(owner, repo)).get("default_branch") or "main"
        response = await self._send(
            f"{self._base}/repos/{owner}/{repo}/branches/{branch}", None
        )
        data = _json(response)
        sha = ((data or {}).get("commit") or {}).get("sha") if isinstance(data, dict) else None
        if not isinstance(sha, str) or not sha:
            raise TransientError("malformed GitHub response (branch head)")
        return sha

    def list_commits(
        self,
        owner: str,
        repo: str,
        *,
        sha: str | None = None,
        since: str | None = None,
        until: str | None = None,
        per_page: int = _DEFAULT_PER_PAGE,
        max_pages: int | None = None,
    ) -> Paginator:
        params: dict[str, Any] = {"per_page": per_page}
        for key, value in (("sha", sha), ("since", since), ("until", until)):
            if value:
                params[key] = value
        return self._paginator(f"/repos/{owner}/{repo}/commits", params, max_pages=max_pages)

    async def compare(
        self,
        owner: str,
        repo: str,
        base: str,
        head: str,
        *,
        per_page: int = _DEFAULT_PER_PAGE,
        max_pages: int | None = None,
    ) -> CompareResult:
        """``compare/{base}...{head}`` with the commit list paginated and merged."""
        url: str | None = f"{self._base}/repos/{owner}/{repo}/compare/{base}...{head}"
        params: dict[str, Any] | None = {"per_page": per_page}
        cap = max_pages or self._max_pages
        result: CompareResult | None = None
        pages = 0
        while url:
            if pages >= cap:
                assert result is not None
                result.exhausted = False
                return result
            response = await self._send(url, params)
            pages += 1
            params = None
            data = _json(response)
            if not isinstance(data, dict) or not isinstance(data.get("commits", []), list):
                raise TransientError("malformed GitHub response (compare)")
            if result is None:
                result = CompareResult(
                    status=str(data.get("status", "")),
                    ahead_by=int(data.get("ahead_by", 0) or 0),
                    behind_by=int(data.get("behind_by", 0) or 0),
                )
            result.commits.extend(data.get("commits", []))
            url = self._extract_next_link(response.headers.get("Link", ""))
        assert result is not None
        return result

    def list_issues(
        self,
        owner: str,
        repo: str,
        *,
        state: str = "all",
        since: str | None = None,
        sort: str = "updated",
        direction: str = "asc",
        per_page: int = _DEFAULT_PER_PAGE,
        max_pages: int | None = None,
    ) -> Paginator:
        """Issues endpoint. Pull requests appear too (``pull_request`` key); the
        caller filters them out."""
        params: dict[str, Any] = {
            "state": state,
            "sort": sort,
            "direction": direction,
            "per_page": per_page,
        }
        if since:
            params["since"] = since
        return self._paginator(f"/repos/{owner}/{repo}/issues", params, max_pages=max_pages)

    def list_issue_comments(
        self,
        owner: str,
        repo: str,
        *,
        since: str | None = None,
        sort: str = "updated",
        direction: str = "asc",
        per_page: int = _DEFAULT_PER_PAGE,
        max_pages: int | None = None,
    ) -> Paginator:
        """Repo-wide issue + PR conversation comments feed."""
        params: dict[str, Any] = {"sort": sort, "direction": direction, "per_page": per_page}
        if since:
            params["since"] = since
        return self._paginator(
            f"/repos/{owner}/{repo}/issues/comments", params, max_pages=max_pages
        )

    def list_review_comments(
        self,
        owner: str,
        repo: str,
        *,
        since: str | None = None,
        sort: str = "updated",
        direction: str = "asc",
        per_page: int = _DEFAULT_PER_PAGE,
        max_pages: int | None = None,
    ) -> Paginator:
        """Repo-wide pull request review (inline) comments feed."""
        params: dict[str, Any] = {"sort": sort, "direction": direction, "per_page": per_page}
        if since:
            params["since"] = since
        return self._paginator(
            f"/repos/{owner}/{repo}/pulls/comments", params, max_pages=max_pages
        )

    def list_pulls(
        self,
        owner: str,
        repo: str,
        *,
        state: str = "all",
        sort: str = "updated",
        direction: str = "desc",
        per_page: int = _DEFAULT_PER_PAGE,
        max_pages: int | None = None,
    ) -> Paginator:
        params = {"state": state, "sort": sort, "direction": direction, "per_page": per_page}
        return self._paginator(f"/repos/{owner}/{repo}/pulls", params, max_pages=max_pages)

    def list_pull_reviews(
        self,
        owner: str,
        repo: str,
        number: int,
        *,
        etag: str | None = None,
        per_page: int = _DEFAULT_PER_PAGE,
        max_pages: int | None = None,
    ) -> Paginator:
        return self._paginator(
            f"/repos/{owner}/{repo}/pulls/{number}/reviews",
            {"per_page": per_page},
            max_pages=max_pages,
            etag=etag,
        )

    def list_pull_issue_comments(
        self,
        owner: str,
        repo: str,
        number: int,
        *,
        etag: str | None = None,
        per_page: int = _DEFAULT_PER_PAGE,
        max_pages: int | None = None,
    ) -> Paginator:
        return self._paginator(
            f"/repos/{owner}/{repo}/issues/{number}/comments",
            {"per_page": per_page},
            max_pages=max_pages,
            etag=etag,
        )

    def list_installation_repositories(
        self, *, per_page: int = _DEFAULT_PER_PAGE, max_pages: int | None = None
    ) -> Paginator:
        """``GET /installation/repositories`` (items are the ``repositories`` list)."""
        return self._paginator(
            "/installation/repositories",
            {"per_page": per_page},
            max_pages=max_pages,
            items_key="repositories",
        )

    async def get_user_by_id(self, user_id: int) -> dict[str, Any]:
        """``GET /user/{id}``: resolves a numeric account id to its current login."""
        response = await self._send(f"{self._base}/user/{int(user_id)}", None)
        data = _json(response)
        if not isinstance(data, dict):
            raise TransientError("malformed GitHub response (user)")
        return data

    async def collaborator_permission(self, owner: str, repo: str, username: str) -> RawResponse:
        """``GET /repos/{o}/{r}/collaborators/{user}/permission``.

        Returns the HTTP status and parsed body with NO interpretation and no
        status-based exception (the verifier decides what each status means). Only a
        transport failure raises (``TransientError``).
        """
        response = await self._send(
            f"{self._base}/repos/{owner}/{repo}/collaborators/{username}/permission",
            None,
            raw=True,
        )
        try:
            body = response.json()
        except ValueError:
            body = None
        return RawResponse(status=response.status_code, body=body)

    async def conditional_get(self, path: str, etag: str | None) -> RawResponse | NotModified:
        """GET *path* with ``If-None-Match``; ``NOT_MODIFIED`` on 304, else the parsed 2xx."""
        response = await self._send(f"{self._base}{path}", None, etag=etag)
        if response.status_code == 304:
            return NOT_MODIFIED
        return RawResponse(response.status_code, _json(response), response.headers.get("ETag"))

    # ── Legacy generators (kept for the pre-existing connector path) ─────────

    async def get_commits(
        self,
        owner: str,
        repo: str,
        since: str | None = None,
        per_page: int = _DEFAULT_PER_PAGE,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield all commits for *owner/repo*, optionally filtered by *since*."""
        async for item in self.list_commits(owner, repo, since=since, per_page=per_page):
            yield item

    async def get_pull_requests(
        self,
        owner: str,
        repo: str,
        state: str = "all",
        since: str | None = None,
        per_page: int = _DEFAULT_PER_PAGE,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield pull requests (client-side ``since``: the REST endpoint has none)."""
        params: dict[str, Any] = {"state": state, "per_page": per_page}
        if since:
            params["sort"] = "updated"
            params["direction"] = "desc"
        async for item in self._paginator(f"/repos/{owner}/{repo}/pulls", params):
            if since and item.get("updated_at", "") < since:
                return
            yield item

    async def get_issues(
        self,
        owner: str,
        repo: str,
        state: str = "closed",
        since: str | None = None,
        per_page: int = _DEFAULT_PER_PAGE,
        max_pages: int = 10,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield issues (pull requests excluded)."""
        async for item in self.list_issues(
            owner,
            repo,
            state=state,
            since=since,
            sort="created",
            direction="desc",
            per_page=per_page,
            max_pages=max_pages,
        ):
            if "pull_request" not in item:
                yield item

    async def get_pr_comments(
        self, owner: str, repo: str, pr_number: int, per_page: int = _DEFAULT_PER_PAGE
    ) -> AsyncIterator[dict[str, Any]]:
        async for item in self.list_pull_issue_comments(
            owner, repo, pr_number, per_page=per_page
        ):
            yield item

    async def get_pr_reviews(
        self, owner: str, repo: str, pr_number: int, per_page: int = _DEFAULT_PER_PAGE
    ) -> AsyncIterator[dict[str, Any]]:
        async for item in self.list_pull_reviews(owner, repo, pr_number, per_page=per_page):
            yield item

    async def get_pr_review_comments(
        self, owner: str, repo: str, pr_number: int, per_page: int = _DEFAULT_PER_PAGE
    ) -> AsyncIterator[dict[str, Any]]:
        async for item in self._paginator(
            f"/repos/{owner}/{repo}/pulls/{pr_number}/comments", {"per_page": per_page}
        ):
            yield item

    # ── Discussions (GraphQL) - legacy; NOT used by the automatic path ───────

    async def get_discussions(
        self,
        owner: str,
        repo: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return up to *limit* discussions via GraphQL (legacy, out of scope)."""
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
                data.get("data", {}).get("repository", {}).get("discussions", {})
            )
            nodes = discussions_data.get("nodes", [])
            results.extend(nodes)

            page_info = discussions_data.get("pageInfo", {})
            if not page_info.get("hasNextPage") or len(results) >= limit:
                break
            cursor = page_info["endCursor"]
            batch_size = min(limit - len(results), 100)

        return results[:limit]

    # ── Request core ──────────────────────────────────────────────────────────

    async def _get_headers(self) -> dict[str, str]:
        """Build authorization headers using a fresh (or cached) token."""
        token = await self._auth.get_token()
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def _send(
        self,
        url: str,
        params: dict[str, Any] | None,
        *,
        etag: str | None = None,
        raw: bool = False,
    ) -> httpx.Response:
        """One logical GET: classify the response, never loop on failure.

        * 401: invalidate the token and retry once; the retry response is classified.
        * Rate limit with an explicit ``Retry-After`` <= 30 s: sleep once and retry
          once; any further (or longer) rate limit raises ``RateLimitedError``.
        * ``raw=True`` returns any HTTP response unclassified (transport errors still
          raise ``TransientError``).
        * 304 is returned to the caller only when *etag* was sent.
        """
        reauthed = False
        slept = False
        while True:
            headers = await self._get_headers()
            if etag:
                headers["If-None-Match"] = etag
            try:
                async with httpx.AsyncClient(
                    timeout=_TIMEOUT, transport=self._transport
                ) as http:
                    response = await http.get(url, headers=headers, params=params)
            except httpx.HTTPError as exc:
                raise TransientError(f"network error: {type(exc).__name__}") from exc

            if raw:
                return response
            status = response.status_code
            if status == 401 and not reauthed:
                reauthed = True
                await self._auth.invalidate_cache()
                continue
            error = self._error_for(response, conditional=bool(etag))
            if error is None:
                return response
            if (
                isinstance(error, RateLimitedError)
                and not slept
                and error.retry_after_s is not None
                and "retry-after" in response.headers
                and error.retry_after_s <= MAX_INLINE_SLEEP_S
            ):
                slept = True
                log.warning("github_rate_limit_inline_sleep", retry_after=error.retry_after_s)
                await self._sleep(error.retry_after_s)
                continue
            raise error

    @staticmethod
    def _error_for(response: httpx.Response, *, conditional: bool) -> GitHubFetchError | None:
        """Map a response to a typed error, or ``None`` when it is usable."""
        status = response.status_code
        if 200 <= status < 300:
            return None
        if status == 304:
            return None if conditional else TransientError("unexpected 304")
        message = _message(response).lower()
        headers = response.headers
        if status == 429 or (
            status == 403
            and (
                headers.get("x-ratelimit-remaining") == "0"
                or "retry-after" in headers
                or "rate limit" in message
            )
        ):
            retry_after: float | None = None
            raw_retry = headers.get("retry-after")
            if raw_retry is not None:
                try:
                    retry_after = max(0.0, float(raw_retry))
                except ValueError:
                    retry_after = None
            reset_at: datetime | None = None
            raw_reset = headers.get("x-ratelimit-reset")
            if raw_reset is not None:
                try:
                    reset_at = datetime.fromtimestamp(int(raw_reset), tz=UTC)
                except (ValueError, OverflowError, OSError):
                    reset_at = None
            if retry_after is None and reset_at is not None:
                retry_after = max(0.0, (reset_at - datetime.now(UTC)).total_seconds())
            return RateLimitedError(retry_after_s=retry_after, reset_at=reset_at)
        if status in (401, 403):
            return PermissionDeniedError(f"HTTP {status}")
        if status == 404:
            return NotFoundError("HTTP 404")
        if status == 410:
            return GoneError("HTTP 410")
        if status >= 500:
            return TransientError(f"HTTP {status}")
        return GitHubFetchError(f"HTTP {status}")

    async def _paginate(
        self,
        url: str,
        params: dict[str, Any],
        max_pages: int = DEFAULT_MAX_PAGES,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield items from a paginated absolute URL (compat helper)."""
        async for item in Paginator(self, url, params, max_pages=max_pages):
            yield item

    async def _graphql(
        self,
        query: str,
        variables: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a GraphQL query (legacy Discussions only)."""
        headers = await self._get_headers()
        async with httpx.AsyncClient(timeout=30.0, transport=self._transport) as client:
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
        """Return the ``rel="next"`` URL from a ``Link`` header, or ``None``."""
        if not link_header:
            return None
        match = re.search(r'<([^>]+)>;\s*rel="next"', link_header)
        return match.group(1) if match else None
