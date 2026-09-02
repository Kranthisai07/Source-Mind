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

import base64
import hashlib
import re
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

# Query-adaptive fusion (D-004). Identifier-bearing queries are reweighted
# toward the keyword arm; everything else keeps the historical equal weighting.
#
# Commit-hash-shaped tokens. The digit and letter lookaheads are load-bearing:
# a bare [0-9a-f]{7,40} also matches ordinary English words built only from the
# hex letters - "defaced", "effaced", "deadbeef" - which would fire the boost on
# prose. Requiring at least one digit AND at least one a-f letter rejects all of
# those while still accepting real abbreviated hashes like 926fa8554175.
_HEX_IDENT = re.compile(
    r"\b(?=[0-9a-f]*[0-9])(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b", re.IGNORECASE
)
# Issue / PR / discussion references: "#36554".
_NUM_IDENT = re.compile(r"#(\d+)")

# Weights applied to each arm's RRF contribution when identifiers are present.
# Ratio is 0.7 keyword / 0.3 semantic: the keyword arm matching an exact
# identifier is near-conclusive evidence, while the dense arm cannot separate
# artifacts that differ only by that identifier (D-002's attractor commits sat
# 0.081 apart before D-001, and the dense arm still ranks the correct one as low
# as 33rd). It is not 1.0/0.0 because the identifier can appear in several
# memories - the PR that closes an issue cites the issue number - and the dense
# arm is what orders those among themselves.
#
# Expressed scaled to sum to 2.0, matching the default 1.0 + 1.0 so that result
# scores stay on one scale whether or not the boost fired. RRF ordering is
# invariant under scaling both weights by a common factor, so 0.6/1.4 ranks
# identically to 0.3/0.7.
_IDENT_W_SEMANTIC = 0.6
_IDENT_W_KEYWORD = 1.4


def _extract_identifiers(query: str) -> list[str]:
    """Structured identifiers in a query, as tsquery-safe lexemes.

    Returns [] for ordinary conversational queries, which is what keeps this
    change inert for them.
    """
    found = _HEX_IDENT.findall(query) + _NUM_IDENT.findall(query)
    # De-duplicate, preserving order, so the tsquery stays minimal.
    seen: dict[str, None] = {}
    for tok in found:
        seen.setdefault(tok.lower(), None)
    return list(seen)


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
                1 - (embedding <=> (:emb)::vector) AS score
            FROM memories
            WHERE workspace_id = (:ws_id)::uuid
              AND current_version = TRUE
              AND deleted_at IS NULL
              AND embedding IS NOT NULL
              AND 1 - (embedding <=> (:emb)::vector) >= :min_sim
            ORDER BY embedding <=> (:emb)::vector
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
    identifiers: list[str] | None = None,
) -> list[dict[str, Any]]:
    """PostgreSQL tsvector BM25 keyword search.

    ``plainto_tsquery`` joins every term with AND, so a conversational wrapper
    around an identifier - "What did commit 926fa8554175 change in X?" becomes
    'commit' & '926fa8554175' & 'chang' & 'x' - fails as a conjunction whenever
    the stored memory happens not to contain one of the filler words. Measured
    on the evaluation corpus, that returned zero rows for 294 of 300 questions
    and left this arm contributing nothing to fusion at all.

    When the caller has detected identifiers, this searches on those alone,
    OR-ed together, under the 'simple' configuration: identifiers must not be
    stemmed, and any one of them matching is meaningful. Without identifiers the
    original AND behaviour is used unchanged.
    """
    if identifiers:
        # Safe to interpolate into tsquery syntax: every token came from
        # _extract_identifiers, so it is [0-9a-f]+ or digits, and the assembled
        # string is still bound as a parameter rather than concatenated into SQL.
        tsquery = " | ".join(identifiers)
        sql = """
            SELECT
                id::text,
                content,
                ts_rank(content_tsv, to_tsquery('simple', :query)) AS score
            FROM memories
            WHERE workspace_id = (:ws_id)::uuid
              AND current_version = TRUE
              AND deleted_at IS NULL
              AND content_tsv @@ to_tsquery('simple', :query)
            ORDER BY score DESC
            LIMIT :limit
        """
    else:
        tsquery = query
        sql = """
            SELECT
                id::text,
                content,
                ts_rank(content_tsv, plainto_tsquery('english', :query)) AS score
            FROM memories
            WHERE workspace_id = (:ws_id)::uuid
              AND current_version = TRUE
              AND deleted_at IS NULL
              AND content_tsv @@ plainto_tsquery('english', :query)
            ORDER BY score DESC
            LIMIT :limit
        """

    result = await session.execute(
        text(sql),
        {
            "query": tsquery,
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
    w_semantic: float = 1.0,
    w_keyword: float = 1.0,
) -> list[dict[str, Any]]:
    """Reciprocal Rank Fusion. See ADR-005, and D-004 for the weights.

    The weights default to 1.0 / 1.0, which is the equal weighting this used
    before they existed - callers that do not pass them get the old behaviour
    exactly.
    """
    scores: dict[str, float] = {}
    content_map: dict[str, str] = {}
    match_types: dict[str, set[str]] = {}

    for rank, item in enumerate(semantic):
        mid = item["id"]
        scores[mid] = scores.get(mid, 0.0) + w_semantic / (_RRF_K + rank + 1)
        content_map[mid] = item["content"]
        match_types.setdefault(mid, set()).add("semantic")

    for rank, item in enumerate(keyword):
        mid = item["id"]
        scores[mid] = scores.get(mid, 0.0) + w_keyword / (_RRF_K + rank + 1)
        content_map.setdefault(mid, item["content"])
        match_types.setdefault(mid, set()).add("keyword")

    ranked = sorted(scores.keys(), key=lambda k: scores[k], reverse=True)
    return [
        {
            "id": mid,
            "content": content_map[mid],
            "score": scores[mid],
            # Emit in the order MatchTypeLiteral declares ("semantic+keyword").
            # sorted() would produce "keyword+semantic" and fail response validation.
            "match_type": "+".join(
                m for m in ("semantic", "keyword") if m in match_types[mid]
            ),
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

    # Shape matters: this feeds MemoryResponse.attribution, typed as
    # list[ContributionBreakdown], which requires a nested `user` object and a
    # `percentage`. The previous flat {contributor, contribution_weight} could
    # never validate, which went unnoticed because the route dropped the value
    # before it reached the response model.
    #
    # DISTINCT ON because `attributions` is append-only: a contributor
    # accumulates one row per recomputation, and without it a single person
    # appears several times and the percentages no longer sum to 100.
    result = await session.execute(
        text("""
            SELECT
                memory_id::text,
                user_id::text,
                contributor,
                avatar_url,
                contribution_weight
            FROM (
                SELECT DISTINCT ON (a.memory_id, a.user_id)
                    a.memory_id,
                    a.user_id,
                    COALESCE(u.display_name, u.email) AS contributor,
                    u.avatar_url,
                    a.contribution_weight
                FROM attributions a
                JOIN users u ON u.id = a.user_id
                WHERE a.memory_id = ANY((:ids)::uuid[])
                ORDER BY a.memory_id, a.user_id, a.created_at DESC
            ) latest
            ORDER BY contribution_weight DESC
        """),
        {"ids": memory_ids},
    )
    attr: dict[str, list[dict[str, Any]]] = {}
    for mem_id, user_id, contributor, avatar_url, weight in result.fetchall():
        bucket = attr.setdefault(mem_id, [])
        bucket.append(
            {
                "user": {
                    "id": user_id,
                    "display_name": contributor,
                    "avatar_url": avatar_url,
                },
                "contribution_weight": float(weight),
                "percentage": round(float(weight) * 100.0, 2),
                # Rows arrive ordered by weight descending, so the first
                # contributor seen for a memory is its primary one.
                "is_primary": len(bucket) == 0,
            }
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

    # Step 0: Detect structured identifiers. Cheap regex, no model call - see
    # D-004 on why the trigger is rule-based rather than a learned classifier.
    identifiers = _extract_identifiers(query)

    # Step 1: Embed query
    embedding, emb_cached = await _get_query_embedding(query, openai_client)

    # Step 2: Run searches
    if mode == "semantic":
        semantic = await _semantic_search(
            session, embedding, workspace_id, _MAX_CANDIDATES, min_similarity
        )
        keyword: list[dict[str, Any]] = []
    elif mode == "keyword":
        semantic = []
        keyword = await _keyword_search(
            session, query, workspace_id, _MAX_CANDIDATES, identifiers
        )
    else:  # hybrid — run sequentially; AsyncSession is not concurrency-safe
        semantic = await _semantic_search(
            session, embedding, workspace_id, _MAX_CANDIDATES, min_similarity
        )
        keyword = await _keyword_search(
            session, query, workspace_id, _MAX_CANDIDATES, identifiers
        )

    # Step 3: Merge via RRF, weighted toward the keyword arm when the query
    # carries an identifier. Without one, both weights stay 1.0 and this is the
    # same equal-weight fusion as before - strictly additive for the common case.
    if identifiers:
        merged = _rrf_merge(
            semantic, keyword, _IDENT_W_SEMANTIC, _IDENT_W_KEYWORD
        )
    else:
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
        identifier_boost=bool(identifiers),
        identifiers_found=len(identifiers),
        keyword_hits=len(keyword),
    )

    return {
        "results": top,
        "total_found": len(merged),
        "search_latency_ms": latency_ms,
        "query_embedding_cached": emb_cached,
        "mode": mode,
    }
