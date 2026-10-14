"""The real FastAPI app with ONLY the GitHub connect hop simulated, for the browser journey.

Real: the application, its routes, auth (real Clerk JWT verification), RLS, Redis, PostgreSQL, the
connect-state/ticket logic. SIMULATED (labelled in the UI by the repository name ``acme/repo`` and the
GitHub login ``octocat-sim``): the two calls that would reach github.com, i.e. the authorization URL
(points back at the local web app) and the code exchange / reachable-repository lookup.
Never import outside the browser journey run.
"""

from __future__ import annotations

import os
from urllib.parse import urlencode

from sourcemind.api.v1 import connectors as connectors_api
from sourcemind.connectors.github import connect_flow
from sourcemind.main import app  # noqa: F401 - the real application

WEB_ORIGIN = os.environ.get("E2E_WEB_ORIGIN", "http://localhost:3000")
BASE_PATH = os.environ.get("E2E_WEB_BASE_PATH", "/Source-Mind")


def _authorize_url(_client_id: str, state: str) -> str:
    query = urlencode({"code": "sim-code", "state": state, "installation_id": "4242"})
    return f"{WEB_ORIGIN}{BASE_PATH}/connectors/github/callback?{query}"


async def _authorize_user(code: str, **_kw: object) -> connect_flow.UserAuthorization:
    if code != "sim-code":
        raise connect_flow.ConnectFlowError("github_code_rejected")
    return connect_flow.UserAuthorization(
        github_user_id=777,
        github_login="octocat-sim",
        installation_id=4242,
        repositories=(connect_flow.ReachableRepository("acme/repo", True, "main"),),
        truncated=False,
    )


connect_flow.build_authorize_url = _authorize_url  # type: ignore[assignment]
connect_flow.authorize_user = _authorize_user  # type: ignore[assignment]
_ = connectors_api


# The query embedding would call the model provider; SIMULATED with a constant vector. Ranking in the
# browser journey therefore comes from keyword matching, not from semantic similarity.
from sourcemind.services.search import hybrid as _hybrid  # noqa: E402


async def _fake_query_embedding(query: str, openai_client: object | None = None):
    return [0.01] * 3072, False


_hybrid._get_query_embedding = _fake_query_embedding  # type: ignore[assignment]


# Every other embedding path (e.g. recomputing a memory's vector when a human edits it) goes through
# EmbeddingService; SIMULATED with constant vectors so nothing is sent to a model provider.
from sourcemind.services.ingestion import embedder as _embedder  # noqa: E402


async def _fake_embed(self, facts):  # type: ignore[no-untyped-def]
    return [
        _embedder.EmbeddingResult(content=f, embedding=[0.01] * 3072, token_count=8, cached=False)
        for f in facts
    ]


_embedder.EmbeddingService.embed = _fake_embed  # type: ignore[method-assign]
