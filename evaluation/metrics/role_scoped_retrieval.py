"""Metric 4: Role-Scoped Retrieval — measures whether retrieval results
are correctly filtered to items authored by or relevant to a specific user role.

Simulates a workspace with two roles (engineer vs. manager) and checks that
queries scoped to a role only return artifacts from that role's "workspace view".

For evaluation, we split the dataset by artifact_type:
  - engineer: commits, pull_requests
  - manager: issues, discussions

Score = fraction of role-scoped queries that return only same-role artifacts.
"""

from __future__ import annotations

from typing import Any, Protocol

# Artifact types visible to each simulated role
ROLE_ARTIFACT_TYPES: dict[str, set[str]] = {
    "engineer": {"commit", "pull_request"},
    "manager": {"issue", "discussion"},
}


class Retriever(Protocol):
    def retrieve(self, query: str, top_k: int = 5) -> list[dict[str, Any]]: ...


def role_scoped_retrieval(
    retriever: Retriever,
    dataset: list[dict[str, Any]],
    role: str = "engineer",
    top_k: int = 5,
) -> dict[str, Any]:
    """Compute role-scoped retrieval precision.

    For each item belonging to ``role``'s artifact types, we retrieve top-k
    results and check what fraction of them also belong to the role's types.

    Args:
        retriever: Any object with a ``retrieve(query, top_k)`` method.
        dataset: Ground-truth items with ``artifact_type`` and ``question`` keys.
        role: Either ``"engineer"`` or ``"manager"``.
        top_k: Number of results per query.

    Returns:
        Dict with ``score`` (0.0–1.0), ``total_queries``, ``in_scope_results``,
        ``total_results``.
    """
    allowed_types = ROLE_ARTIFACT_TYPES.get(role, set())
    role_items = [i for i in dataset if i.get("artifact_type") in allowed_types]

    if not role_items:
        return {
            "metric": "role_scoped_retrieval",
            "role": role,
            "score": 0.0,
            "total_queries": 0,
            "in_scope_results": 0,
            "total_results": 0,
        }

    in_scope = 0
    total_results = 0

    for item in role_items:
        results = retriever.retrieve(item["question"], top_k=top_k)
        for r in results:
            total_results += 1
            r_type = (r.get("metadata") or {}).get("artifact_type", "")
            if r_type in allowed_types:
                in_scope += 1

    score = in_scope / total_results if total_results > 0 else 0.0

    return {
        "metric": "role_scoped_retrieval",
        "role": role,
        "score": round(score, 4),
        "total_queries": len(role_items),
        "in_scope_results": in_scope,
        "total_results": total_results,
    }
