"""
Hybrid search: pgvector cosine similarity + PostgreSQL BM25 (tsvector),
merged via Reciprocal Rank Fusion (RRF).

Latency targets: p50 < 200ms, p95 < 500ms
See ADR-005 for rationale on RRF over learned sparse retrieval.

Access control:
  - Results always scoped to requesting user's workspace
  - 'viewer' role: content truncated to 200 chars
  - Other roles: full content
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import struct
import time
import uuid
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.core.redis_client import get_redis

log = structlog.get_logger(__name__)

_EMBED_MODEL = "text-embedding-3-large"
_EMBED_DIMS = 3072
_EMBED_CACHE_TTL = 60 * 60 * 24 * 7  # 7 days for queries
_RRF_K = 60
_MAX_CANDIDATES = 50
_VIEWER_LIMIT = 200


def _emb_cache_key(content: str) -> str:
    h = hashlib.sha256(content.encode()).hexdigest()
    return f"emb:v1:{h}"


def _pack(embedding: list[float]) -> str:
    raw = struct.pack(f"{len(embedding)}f", *embedding)
    return base64.b64encode(raw).decode("ascii")


def _unpack(data: str) -> list[float]:
    raw = base64.b64decode(data.encode("ascii"))
    count = len(raw) // 4
    return list(struct.unpack(f"{count}f", raw))


async def _get_query_embedding(
    query: str, openai_client: object | None = None
) -> tuple[list[float], bool]:
    """Embed query, using Redis cache. Returns (embedding, was_cached)."""
    redis = get_redis()
    key = _emb_cache_key(query)

    cached = await redis.get(key)
    if cached:
        return _unpack(cached), True

    if openai_client is None:
        from openai import AsyncOpenAI
        from sourcemind.core.config import get_settings
        settings = get_settings()
        openai_client = AsyncOpenAI(api_key=settings.openai_api_key)

    response = await openai_client.embeddings.create(  # type: ignore[union-attr]
        model=_EMBED_MODEL, input=[query], dimensions=_EMBED_DIMS
    )
    embedding = response.data[0].embedding
    await redis.setex(key, _EMBED_CACHE_TTL, _pack(embedding))
    return embedding, False


async def _semantic_search(
    session: AsyncSession,
    embedding: list[float],
    workspace_id: uuid.UUID,
    limit: int,
    min_similarity: float,
) -> list[dict[str, Any]]:
    """pgvector cosine similarity search."""
    embedding_str = "[" + ",".join(str(f) for f in embedding) + "]"

    result = await session.execute(
        text("""
            SELECT
                id::text,
                content,
                1 - (embedding <=> :emb::vector) AS score
            FROM memories
            WHERE workspace_id = :ws_id::uuid
              AND current_version = TRUE
              AND deleted_at IS NULL
              AND embedding IS NOT NULL
              AND 1 - (embedding <=> :emb::vector) >= :min_sim
            ORDER BY embedding <=> :emb::vector
            LIMIT :limit
        """),
        {
            "emb": embedding_str,
            "ws_id": str(workspace_id),
            "min_sim": min_similarity,
            "limit": limit,
        },
    )
    return [
        {
            "id": row[0],
            "content": row[1],
            "score": float(row[2]),
            "match_type": "semantic",
        }
        for row in result.fetchall()
    ]


async def _keyword_search(
    session: AsyncSession,
    query: str,
    workspace_id: uuid.UUID,
    limit: int,
) -> list[dict[str, Any]]:
    """PostgreSQL tsvector BM25 keyword search."""
    result = await session.execute(
        text("""
            SELECT
                id::text,
                content,
                ts_rank(content_tsv, plainto_tsquery('english', :query)) AS score
            FROM memories
            WHERE workspace_id = :ws_id::uuid
              AND current_version = TRUE
              AND deleted_at IS NULL
              AND content_tsv @@ plainto_tsquery('english', :query)
            ORDER BY score DESC
            LIMIT :limit
        """),
        {
            "query": query,
            "ws_id": str(workspace_id),
            "limit": limit,
        },
    )
    return [
        {
            "id": row[0],
            "content": row[1],
            "score": float(row[2]),
            "match_type": "keyword",
        }
        for row in result.fetchall()
    ]


def _rrf_merge(
    semantic: list[dict[str, Any]],
    keyword: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Reciprocal Rank Fusion. See ADR-005."""
    scores: dict[str, float] = {}
    content_map: dict[str, str] = {}
    match_types: dict[str, set[str]] = {}

    for rank, item in enumerate(semantic):
        mid = item["id"]
        scores[mid] = scores.get(mid, 0.0) + 1.0 / (_RRF_K + rank + 1)
        content_map[mid] = item["content"]
        match_types.setdefault(mid, set()).add("semantic")

    for rank, item in enumerate(keyword):
        mid = item["id"]
        scores[mid] = scores.get(mid, 0.0) + 1.0 / (_RRF_K + rank + 1)
        content_map.setdefault(mid, item["content"])
        match_types.setdefault(mid, set()).add("keyword")

    ranked = sorted(scores.keys(), key=lambda k: scores[k], reverse=True)
    return [
        {
            "id": mid,
            "content": content_map[mid],
            "score": scores[mid],
            "match_type": "+".join(sorted(match_types[mid])),
        }
        for mid in ranked
    ]


async def _fetch_attributions(
    session: AsyncSession,
    memory_ids: list[str],
) -> dict[str, list[dict[str, Any]]]:
    """Batch-fetch attribution data for a list of memory IDs."""
    if not memory_ids:
        return {}

    result = await session.execute(
        text("""
            SELECT
                a.memory_id::text,
                COALESCE(u.display_name, u.email) AS contributor,
                a.contribution_weight
            FROM attributions a
            JOIN users u ON u.id = a.user_id
            WHERE a.memory_id = ANY(:ids::uuid[])
            ORDER BY a.contribution_weight DESC
        """),
        {"ids": memory_ids},
    )
    attr: dict[str, list[dict[str, Any]]] = {}
    for mem_id, contributor, weight in result.fetchall():
        attr.setdefault(mem_id, []).append(
            {"contributor": contributor, "contribution_weight": float(weight)}
        )
    return attr


async def hybrid_search(
    session: AsyncSession,
    query: str,
    workspace_id: uuid.UUID,
    limit: int = 20,
    min_similarity: float = 0.3,
    mode: str = "hybrid",
    user_role: str = "member",
    include_attribution: bool = False,
    openai_client: object | None = None,
) -> dict[str, Any]:
    """
    Full hybrid search with RRF merging.

    openai_client: injected via FastAPI Depends in route handlers; pass None to
    auto-instantiate from settings (backward compatible for direct calls).

    Returns a dict with: results, total_found, search_latency_ms,
    query_embedding_cached, mode.
    """
    t0 = time.monotonic()

    # Step 1: Embed query
    embedding, emb_cached = await _get_query_embedding(query, openai_client)

    # Step 2: Run searches
    if mode == "semantic":
        semantic = await _semantic_search(session, embedding, workspace_id, _MAX_CANDIDATES, min_similarity)
        keyword: list[dict[str, Any]] = []
    elif mode == "keyword":
        semantic = []
        keyword = await _keyword_search(session, query, workspace_id, _MAX_CANDIDATES)
    else:  # hybrid
        semantic, keyword = await asyncio.gather(
            _semantic_search(session, embedding, workspace_id, _MAX_CANDIDATES, min_similarity),
            _keyword_search(session, query, workspace_id, _MAX_CANDIDATES),
        )

    # Step 3: Merge via RRF
    merged = _rrf_merge(semantic, keyword)
    top = merged[:limit]

    # Step 4: Access control
    if user_role == "viewer":
        for item in top:
            if len(item["content"]) > _VIEWER_LIMIT:
                item["content"] = (
                    item["content"][:_VIEWER_LIMIT]
                    + " [Summary only — request access for full content]"
                )

    # Step 5: Attribution enrichment
    if include_attribution and top:
        attr_data = await _fetch_attributions(session, [item["id"] for item in top])
        for item in top:
            item["attribution"] = attr_data.get(item["id"])

    latency_ms = int((time.monotonic() - t0) * 1000)
    log.info(
        "hybrid_search_complete",
        query_len=len(query),
        mode=mode,
        total_found=len(merged),
        returned=len(top),
        latency_ms=latency_ms,
        emb_cached=emb_cached,
    )

    return {
        "results": top,
        "total_found": len(merged),
        "search_latency_ms": latency_ms,
        "query_embedding_cached": emb_cached,
        "mode": mode,
    }
