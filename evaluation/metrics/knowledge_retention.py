"""Metric 1: Knowledge Retention — measures what fraction of ingested artifacts
are retrievable via top-k search.

Score = |correctly_retrieved| / |total_queries|

A query is "correctly retrieved" if the expected artifact appears in the top-k
results (recall@k).
"""

from __future__ import annotations

from typing import Any, Protocol


class Retriever(Protocol):
    def retrieve(self, query: str, top_k: int = 5) -> list[dict[str, Any]]: ...


def knowledge_retention(
    retriever: Retriever,
    dataset: list[dict[str, Any]],
    top_k: int = 5,
) -> dict[str, Any]:
    """Compute knowledge retention (recall@k) over the dataset.

    Args:
        retriever: Any object with a ``retrieve(query, top_k)`` method.
        dataset: List of ground-truth items with ``id``, ``question``,
                 ``expected_answer`` keys.
        top_k: Number of results to retrieve per query.

    Returns:
        Dict with ``score`` (0.0–1.0), ``hits``, ``total``.
    """
    hits = 0
    total = len(dataset)
    if total == 0:
        return {"score": 0.0, "hits": 0, "total": 0, "metric": "knowledge_retention"}

    for item in dataset:
        results = retriever.retrieve(item["question"], top_k=top_k)
        retrieved_ids = {r["id"] for r in results}
        if item["id"] in retrieved_ids:
            hits += 1

    score = hits / total
    return {
        "metric": "knowledge_retention",
        "score": round(score, 4),
        "hits": hits,
        "total": total,
        "top_k": top_k,
    }
