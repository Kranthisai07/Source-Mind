"""GitHub App user authorization used once at connect time (contract C11).

The connecting admin authorizes the GitHub App; the callback exchanges the one-time
``code`` for a *user access token*. Per GitHub's documentation a GitHub App user access
token reaches only the repositories that BOTH the user and the app can access, so the
repository list returned for that token is the user-and-installation intersection. The
token is used for the calls below and then dropped: it is a local variable, never
returned, stored or logged.

This module makes its own small httpx calls so it does not touch ``client.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
import structlog

log = structlog.get_logger(__name__)

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"  # noqa: S105 - a URL, not a secret
API_BASE = "https://api.github.com"
_PER_PAGE = 100
_MAX_INSTALLATION_PAGES = 5
_MAX_REPO_PAGES = 20
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class ConnectFlowError(Exception):
    """A connect-flow failure with a stable, non-sensitive ``code``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ReachableRepository:
    full_name: str
    private: bool
    default_branch: str | None


@dataclass(frozen=True)
class UserAuthorization:
    """What the user token proved. Deliberately has no token field."""

    github_user_id: int
    github_login: str | None
    installation_id: int
    repositories: tuple[ReachableRepository, ...]
    truncated: bool


def build_authorize_url(client_id: str, state: str) -> str:
    """URL the browser is sent to for GitHub App user authorization."""
    return f"{AUTHORIZE_URL}?{urlencode({'client_id': client_id, 'state': state})}"


async def _get_json(
    http: httpx.AsyncClient, path: str, token: str, params: dict[str, int] | None = None
) -> dict:
    try:
        response = await http.get(
            f"{API_BASE}{path}",
            params=params,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
    except httpx.HTTPError:
        raise ConnectFlowError("github_unavailable") from None
    if response.status_code in (401, 403):
        raise ConnectFlowError("github_denied")
    if response.status_code != 200:
        raise ConnectFlowError("github_unavailable")
    try:
        payload = response.json()
    except ValueError:
        raise ConnectFlowError("github_bad_response") from None
    if not isinstance(payload, dict):
        raise ConnectFlowError("github_bad_response")
    return payload


async def _exchange_code(
    http: httpx.AsyncClient, *, code: str, client_id: str, client_secret: str
) -> str:
    try:
        response = await http.post(
            TOKEN_URL,
            data={"client_id": client_id, "client_secret": client_secret, "code": code},
            headers={"Accept": "application/json"},
        )
    except httpx.HTTPError:
        raise ConnectFlowError("github_unavailable") from None
    if response.status_code != 200:
        raise ConnectFlowError("github_unavailable")
    try:
        payload = response.json()
    except ValueError:
        raise ConnectFlowError("github_bad_response") from None
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token:
        # GitHub answers 200 with {"error": "bad_verification_code"} for a bad code.
        raise ConnectFlowError("code_rejected")
    return token


async def _list_installation_ids(http: httpx.AsyncClient, token: str) -> list[int]:
    ids: list[int] = []
    for page in range(1, _MAX_INSTALLATION_PAGES + 1):
        payload = await _get_json(
            http, "/user/installations", token, {"per_page": _PER_PAGE, "page": page}
        )
        rows = payload.get("installations")
        if not isinstance(rows, list):
            raise ConnectFlowError("github_bad_response")
        ids.extend(int(row["id"]) for row in rows if isinstance(row, dict) and "id" in row)
        if len(rows) < _PER_PAGE:
            break
    return ids


async def _list_repositories(
    http: httpx.AsyncClient, token: str, installation_id: int
) -> tuple[list[ReachableRepository], bool]:
    repos: list[ReachableRepository] = []
    for page in range(1, _MAX_REPO_PAGES + 1):
        payload = await _get_json(
            http,
            f"/user/installations/{installation_id}/repositories",
            token,
            {"per_page": _PER_PAGE, "page": page},
        )
        rows = payload.get("repositories")
        if not isinstance(rows, list):
            raise ConnectFlowError("github_bad_response")
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("full_name"), str):
                branch = row.get("default_branch")
                repos.append(
                    ReachableRepository(
                        full_name=row["full_name"],
                        private=bool(row.get("private", False)),
                        default_branch=branch if isinstance(branch, str) else None,
                    )
                )
        if len(rows) < _PER_PAGE:
            return repos, False
    return repos, True


async def authorize_user(
    code: str,
    *,
    client_id: str,
    client_secret: str,
    installation_id: int | None = None,
    http: httpx.AsyncClient | None = None,
) -> UserAuthorization:
    """Exchange ``code`` and report the user's reachable repositories for one installation.

    Raises ``ConnectFlowError`` (stable ``code``) on any failure. ``http`` is injectable
    so tests can pass an ``httpx.MockTransport`` client.
    """
    if not client_id or not client_secret:
        raise ConnectFlowError("not_configured")
    owned = http is None
    client = http or httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        token = await _exchange_code(
            client, code=code, client_id=client_id, client_secret=client_secret
        )
        try:
            user = await _get_json(client, "/user", token)
            github_user_id = user.get("id")
            if not isinstance(github_user_id, int) or github_user_id <= 0:
                raise ConnectFlowError("github_bad_response")
            login = user.get("login")

            reachable = await _list_installation_ids(client, token)
            if not reachable:
                raise ConnectFlowError("no_installation")
            if installation_id is None:
                if len(reachable) != 1:
                    raise ConnectFlowError("installation_required")
                installation_id = reachable[0]
            elif installation_id not in reachable:
                raise ConnectFlowError("installation_not_reachable")

            repositories, truncated = await _list_repositories(
                client, token, installation_id
            )
        finally:
            # The token is never needed again; drop the only reference we hold.
            del token
    finally:
        if owned:
            await client.aclose()

    log.info(
        "github_connect_authorized",
        installation_id=installation_id,
        repositories=len(repositories),
        truncated=truncated,
    )
    return UserAuthorization(
        github_user_id=github_user_id,
        github_login=login if isinstance(login, str) else None,
        installation_id=installation_id,
        repositories=tuple(repositories),
        truncated=truncated,
    )
