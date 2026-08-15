"""Supermemory baseline — stub for comparing against Supermemory's API.

Supermemory (https://supermemory.ai) is a memory-as-a-service API.
This baseline wraps its REST API so the evaluation runner can query it
alongside SourceMind and naive RAG.

If ``SUPERMEMORY_API_KEY`` is not set in the environment, all operations
are no-ops and the baseline returns empty results with a ``skipped`` flag.
"""

from __future__ import annotations

import os
from typing import Any

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

_API_BASE = "https://api.supermemory.ai/v3"


class SupermemoryBaseline:
    """Wraps Supermemory REST API for evaluation benchmarking.

    Args:
        api_key: Supermemory API key; falls back to ``SUPERMEMORY_API_KEY`` env var.
        space_id: Optional Supermemory space (collection) to scope operations.
    """

    def __init__(
        self,
        api_key: str | None = None,
        space_id: str | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("SUPERMEMORY_API_KEY", "")
        self._space_id = space_id
        self._available = bool(self._api_key) and HTTPX_AVAILABLE
        self._headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    @property
    def available(self) -> bool:
        """Return True if the baseline is configured and ready."""
        return self._available

    def index(self, items: list[dict[str, Any]]) -> int:
        """Add items to Supermemory (synchronous wrapper).

        Args:
            items: List of dicts with at least ``id``, ``content`` keys.

        Returns:
            Number of items successfully added.
        """
        if not self._available:
            return 0
        import asyncio
        return asyncio.get_event_loop().run_until_complete(self._async_index(items))

    async def _async_index(self, items: list[dict[str, Any]]) -> int:
        """Add items via the Supermemory Memories API."""
        added = 0
        async with httpx.AsyncClient(headers=self._headers, timeout=30.0) as client:
            for item in items:
                payload: dict[str, Any] = {
                    "content": item["content"],
                    "metadata": {
                        "sourceId": item.get("id", ""),
                        "repo": item.get("repo", ""),
                        "artifactType": item.get("artifact_type", ""),
                    },
                }
                if self._space_id:
                    payload["spaces"] = [self._space_id]
                try:
                    resp = await client.post(f"{_API_BASE}/memories", json=payload)
                    if resp.status_code in (200, 201):
                        added += 1
                except Exception:
                    pass
        return added

    def retrieve(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Query Supermemory for relevant memories (synchronous wrapper).

        Args:
            query: Natural language query string.
            top_k: Maximum number of results.

        Returns:
            List of result dicts with ``id``, ``content``, ``score`` keys,
            or empty list if unavailable.
        """
        if not self._available:
            return []
        import asyncio
        return asyncio.get_event_loop().run_until_complete(
            self._async_retrieve(query, top_k)
        )

    async def _async_retrieve(self, query: str, top_k: int) -> list[dict[str, Any]]:
        """Search via the Supermemory Search API."""
        params: dict[str, Any] = {"q": query, "limit": top_k}
        if self._space_id:
            params["spaces"] = self._space_id
        async with httpx.AsyncClient(headers=self._headers, timeout=30.0) as client:
            try:
                resp = await client.get(f"{_API_BASE}/memories/search", params=params)
                if resp.status_code != 200:
                    return []
                data = resp.json()
                results = data.get("results") or data.get("memories") or []
                return [
                    {
                        "id": r.get("id", ""),
                        "content": r.get("content", r.get("text", "")),
                        "score": r.get("score", 0.0),
                        "metadata": r.get("metadata", {}),
                    }
                    for r in results[:top_k]
                ]
            except Exception:
                return []
