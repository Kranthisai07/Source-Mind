"""GitHub PAT authentication for public repo research mining.

Used only for evaluation dataset building — no JWT, no installation tokens,
no Redis cache needed.
"""

from __future__ import annotations


class GitHubPATAuth:
    """Simple PAT authentication for public repo research mining.

    No JWT, no installation tokens, no Redis cache needed.
    Used only for evaluation dataset building.

    Args:
        token: GitHub Personal Access Token.
    """

    def __init__(self, token: str) -> None:
        self._token = token

    async def get_token(self) -> str:
        return self._token
