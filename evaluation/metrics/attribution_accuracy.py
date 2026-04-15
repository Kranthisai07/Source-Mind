"""Metric 2: Attribution Accuracy — measures how accurately the system
attributes retrieved memories to their original authors.

For each item with a known ``source_author``, we check whether the top-1
retrieved result's attribution matches the ground-truth author.

Score = correct_attributions / attributable_items
"""

from __future__ import annotations

from typing import Any, Protocol


class AttributingRetriever(Protocol):
    def retrieve(self, query: str, top_k: int = 5) -> list[dict[str, Any]]: ...


def attribution_accuracy(
    retriever: AttributingRetriever,
    dataset: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute attribution accuracy for items with known authors.

    Args:
        retriever: Retriever whose results include ``metadata.author`` or
                   ``source_author`` fields.
        dataset: Ground-truth items; only items with non-empty
                 ``metadata.author`` are evaluated.

    Returns:
        Dict with ``score`` (0.0–1.0), ``correct``, ``total``.
    """
    correct = 0
    total = 0

    for item in dataset:
        expected_author = (item.get("metadata") or {}).get("author")
        if not expected_author:
            continue
        total += 1

        results = retriever.retrieve(item["question"], top_k=1)
        if not results:
            continue

        top = results[0]
        retrieved_author = (
            top.get("metadata", {}).get("author")
            or top.get("source_author")
            or ""
        )
        if retrieved_author.lower() == expected_author.lower():
            correct += 1

    if total == 0:
        return {"score": 0.0, "correct": 0, "total": 0, "metric": "attribution_accuracy"}

    return {
        "metric": "attribution_accuracy",
        "score": round(correct / total, 4),
        "correct": correct,
        "total": total,
    }
