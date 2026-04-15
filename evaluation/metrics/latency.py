"""Metric 5: Latency — measures query response time in milliseconds.

Runs N queries against a retriever and reports p50, p95, p99 latency
along with min, max, and mean.
"""

from __future__ import annotations

import statistics
import time
from typing import Any, Protocol


class Retriever(Protocol):
    def retrieve(self, query: str, top_k: int = 5) -> list[dict[str, Any]]: ...


def measure_latency(
    retriever: Retriever,
    queries: list[str],
    top_k: int = 5,
    warmup: int = 2,
) -> dict[str, Any]:
    """Measure retrieval latency statistics over a set of queries.

    Args:
        retriever: Any object with a ``retrieve(query, top_k)`` method.
        queries: List of query strings to benchmark.
        top_k: Number of results per query.
        warmup: Number of warmup queries to discard before measuring.

    Returns:
        Dict with latency stats (all in milliseconds):
        ``p50``, ``p95``, ``p99``, ``mean``, ``min``, ``max``, ``total_queries``.
    """
    if not queries:
        return {
            "metric": "latency",
            "p50_ms": 0.0,
            "p95_ms": 0.0,
            "p99_ms": 0.0,
            "mean_ms": 0.0,
            "min_ms": 0.0,
            "max_ms": 0.0,
            "total_queries": 0,
        }

    # Warmup runs (not measured)
    for q in queries[:warmup]:
        retriever.retrieve(q, top_k=top_k)

    latencies_ms: list[float] = []
    for q in queries:
        t0 = time.perf_counter()
        retriever.retrieve(q, top_k=top_k)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        latencies_ms.append(elapsed_ms)

    latencies_ms.sort()
    n = len(latencies_ms)

    def percentile(p: float) -> float:
        idx = int(n * p / 100)
        return latencies_ms[min(idx, n - 1)]

    return {
        "metric": "latency",
        "p50_ms": round(percentile(50), 2),
        "p95_ms": round(percentile(95), 2),
        "p99_ms": round(percentile(99), 2),
        "mean_ms": round(statistics.mean(latencies_ms), 2),
        "min_ms": round(min(latencies_ms), 2),
        "max_ms": round(max(latencies_ms), 2),
        "total_queries": n,
    }
