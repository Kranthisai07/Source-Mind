"""Repository access verifier (contract C11).

Strategy ``collaborator_permission``: only an explicit, parsed 200 with a read-or-better
permission authorizes. An explicit ``none`` is a definitive denial ONLY once the
owner's live verification has flipped ``denial_semantics_verified``; until then it is
reported as unverified. Everything else is UNVERIFIED with a specific reason code. The
mechanism itself is NOT established by GitHub's documentation; fake-server tests prove
only this mapping, not GitHub's behavior.
"""

from __future__ import annotations

import httpx
import structlog

from sourcemind.connectors.github.client import GitHubClient
from sourcemind.connectors.github.contracts import (
    AccessVerdict,
    GitHubFetchError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitedError,
    VerifierResult,
)

log = structlog.get_logger(__name__)

_GRANTED = frozenset({"read", "write", "admin"})
# role_name values that are at least read-equivalent.
_ROLE_GRANTED = frozenset({"read", "triage", "write", "maintain", "admin"})


def _unverified(reason: str) -> VerifierResult:
    return VerifierResult(AccessVerdict.UNVERIFIED, reason)


class CollaboratorPermissionVerifier:
    """``RepoAccessVerifier`` backed by the collaborator-permission endpoint."""

    def __init__(
        self,
        client: GitHubClient,
        *,
        denial_semantics_verified: bool,
        public_repos_exempt: bool = False,
    ) -> None:
        self._client = client
        self._denial_verified = denial_semantics_verified
        self._public_exempt = public_repos_exempt

    async def verify(self, *, owner: str, repo: str, github_user_id: int) -> VerifierResult:
        try:
            return await self._verify(owner, repo, github_user_id)
        except RateLimitedError:
            return _unverified("rate_limited")
        except PermissionDeniedError:
            return _unverified("verifier_forbidden")
        except NotFoundError:
            return _unverified("ambiguous_404")
        except GitHubFetchError:
            return _unverified("verifier_unavailable")
        except (httpx.HTTPError, TimeoutError, OSError):
            return _unverified("verifier_timeout")
        except Exception as exc:  # the verifier must never raise
            log.warning("github_verifier_unexpected", error_type=type(exc).__name__)
            return _unverified("verifier_error")

    async def _verify(self, owner: str, repo: str, github_user_id: int) -> VerifierResult:
        if self._public_exempt:
            repo_info = await self._client.get_repo(owner, repo)
            if repo_info.get("private") is False:
                return VerifierResult(AccessVerdict.AUTHORIZED, "public_repo")

        user = await self._client.get_user_by_id(github_user_id)
        login = user.get("login")
        if not isinstance(login, str) or not login:
            return _unverified("malformed_response")

        response = await self._client.collaborator_permission(owner, repo, login)
        status = response.status
        if status == 200:
            return self._interpret_200(response.body)
        if status == 404:
            return _unverified("ambiguous_404")
        if status in (401, 403):
            return _unverified("verifier_forbidden")
        if status == 429:
            return _unverified("rate_limited")
        if status >= 500:
            return _unverified("verifier_unavailable")
        return _unverified(f"unexpected_status_{status}")

    def _interpret_200(self, body: object) -> VerifierResult:
        if not isinstance(body, dict):
            return _unverified("malformed_response")
        permission = body.get("permission")
        role = body.get("role_name")
        if isinstance(permission, str) and permission in _GRANTED:
            return VerifierResult(AccessVerdict.AUTHORIZED, "ok")
        if permission is None and isinstance(role, str) and role in _ROLE_GRANTED:
            return VerifierResult(AccessVerdict.AUTHORIZED, "ok")
        if permission == "none":
            if self._denial_verified:
                return VerifierResult(AccessVerdict.DENIED, "denied")
            return _unverified("denial_semantics_unverified")
        return _unverified("malformed_response")
