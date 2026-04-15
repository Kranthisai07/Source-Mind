"""Evaluation runner — orchestrates all baselines against all 5 metrics.

Usage:
    python -m evaluation.runner \
        --ground-truth evaluation/data/ground_truth.json \
        --output evaluation/data/results.json

Environment variables required for live baselines:
    OPENAI_API_KEY   — for NaiveRAG embeddings
    SOURCEMIND_API_URL, SOURCEMIND_API_KEY  — for SourceMind (optional)
    SUPERMEMORY_API_KEY — for Supermemory baseline (optional)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evaluation.metrics.attribution_accuracy import attribution_accuracy
from evaluation.metrics.conflict_detection import build_conflict_pairs, conflict_detection_f1
from evaluation.metrics.knowledge_retention import knowledge_retention
from evaluation.metrics.latency import measure_latency
from evaluation.metrics.role_scoped_retrieval import role_scoped_retrieval


# ── SourceMind retriever adapter ──────────────────────────────────────────────

class SourceMindRetriever:
    """Wraps SourceMind's hybrid search endpoint for evaluation.

    Args:
        api_url: Base URL of the SourceMind API (e.g. ``http://localhost:8000``).
        api_key: Bearer token for authentication.
        workspace_id: Workspace ID to search within.
    """

    def __init__(self, api_url: str, api_key: str, workspace_id: str) -> None:
        self._api_url = api_url.rstrip("/")
        self._api_key = api_key
        self._workspace_id = workspace_id

    def retrieve(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        import httpx
        try:
            response = httpx.post(
                f"{self._api_url}/v1/memories/search",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"query": query, "workspace_id": self._workspace_id, "limit": top_k},
                timeout=15.0,
            )
            response.raise_for_status()
            data = response.json()
            results = data.get("data", {}).get("results", [])
            return [
                {
                    "id": r.get("id", ""),
                    "content": r.get("content", ""),
                    "score": r.get("score", 0.0),
                    "metadata": r.get("metadata", {}),
                }
                for r in results[:top_k]
            ]
        except Exception:
            return []


# ── Runner ────────────────────────────────────────────────────────────────────

def run_evaluation(
    dataset: list[dict[str, Any]],
    retrievers: dict[str, Any],
) -> dict[str, Any]:
    """Run all 5 metrics for every retriever in ``retrievers``.

    Args:
        dataset: Ground-truth items list.
        retrievers: Dict of ``{name: retriever_instance}``.

    Returns:
        Nested results dict: ``{retriever_name: {metric_name: result_dict}}``.
    """
    all_results: dict[str, Any] = {}
    queries = [item["question"] for item in dataset]

    # Conflict pairs (shared across retrievers)
    conflict_pairs = build_conflict_pairs(dataset)

    for name, retriever in retrievers.items():
        print(f"\n  Evaluating: {name}")
        results: dict[str, Any] = {}

        print("    → knowledge_retention...")
        results["knowledge_retention"] = knowledge_retention(retriever, dataset, top_k=5)

        print("    → attribution_accuracy...")
        results["attribution_accuracy"] = attribution_accuracy(retriever, dataset)

        print("    → role_scoped_retrieval (engineer)...")
        results["role_scoped_retrieval_engineer"] = role_scoped_retrieval(
            retriever, dataset, role="engineer"
        )
        print("    → role_scoped_retrieval (manager)...")
        results["role_scoped_retrieval_manager"] = role_scoped_retrieval(
            retriever, dataset, role="manager"
        )

        print("    → latency...")
        results["latency"] = measure_latency(retriever, queries[:50], top_k=5)

        if conflict_pairs:
            print("    → conflict_detection (stub)...")
            # For now we use a uniform prediction of False (no conflict detection)
            # — this establishes the baseline. SourceMind's conflict service
            # should be wired here once the evaluation endpoint is exposed.
            preds = [False] * len(conflict_pairs)
            gt = [pair[2] for pair in conflict_pairs]
            results["conflict_detection"] = conflict_detection_f1(preds, gt)

        all_results[name] = results
        kr = results["knowledge_retention"]["score"]
        lat = results["latency"]["p95_ms"]
        print(f"    recall@5={kr:.3f}  p95={lat:.0f}ms")

    return all_results


def main(ground_truth_path: str, output_path: str) -> None:
    gt_file = Path(ground_truth_path)
    if not gt_file.exists():
        print(f"ERROR: Ground truth file not found: {gt_file}", file=sys.stderr)
        sys.exit(1)

    with gt_file.open(encoding="utf-8") as f:
        gt_data = json.load(f)
    dataset = gt_data.get("items", gt_data)
    print(f"Loaded {len(dataset)} ground-truth items.")

    retrievers: dict[str, Any] = {}

    # Naive RAG
    try:
        from evaluation.baselines.naive_rag import NaiveRAGBaseline
        rag = NaiveRAGBaseline()
        print("Indexing NaiveRAG...")
        rag.index(dataset)
        retrievers["naive_rag"] = rag
    except Exception as e:
        print(f"  NaiveRAG unavailable: {e}")

    # Supermemory
    sm_key = os.environ.get("SUPERMEMORY_API_KEY")
    if sm_key:
        try:
            from evaluation.baselines.supermemory_baseline import SupermemoryBaseline
            sm = SupermemoryBaseline(api_key=sm_key)
            print("Indexing Supermemory...")
            sm.index(dataset)
            retrievers["supermemory"] = sm
        except Exception as e:
            print(f"  Supermemory unavailable: {e}")

    # SourceMind
    sm_url = os.environ.get("SOURCEMIND_API_URL")
    sm_api_key = os.environ.get("SOURCEMIND_API_KEY")
    sm_workspace = os.environ.get("SOURCEMIND_WORKSPACE_ID")
    if sm_url and sm_api_key and sm_workspace:
        retrievers["sourcemind"] = SourceMindRetriever(sm_url, sm_api_key, sm_workspace)
    else:
        print("  SourceMind: SOURCEMIND_API_URL/KEY/WORKSPACE_ID not set — skipping")

    if not retrievers:
        print("ERROR: No retrievers configured. Set OPENAI_API_KEY for NaiveRAG.", file=sys.stderr)
        sys.exit(1)

    print(f"\nRunning evaluation with {len(retrievers)} retriever(s)...")
    results = run_evaluation(dataset, retrievers)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ground_truth_items": len(dataset),
        "retrievers": list(results.keys()),
        "results": results,
    }

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\nResults written to {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run SourceMind evaluation harness")
    parser.add_argument("--ground-truth", default="evaluation/data/ground_truth.json")
    parser.add_argument("--output", default="evaluation/data/results.json")
    args = parser.parse_args()
    main(args.ground_truth, args.output)
