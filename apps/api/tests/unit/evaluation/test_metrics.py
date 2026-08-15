"""Unit tests for evaluation metrics."""

from __future__ import annotations

import sys
import os
import pytest

# Make evaluation package importable from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_dataset(n: int = 5) -> list[dict]:
    return [
        {
            "id": f"repo/issues/{i}",
            "repo": "acme/repo",
            "artifact_type": "issue",
            "source_id": str(i),
            "content": f"Issue {i}: content here",
            "question": f"What is issue {i}?",
            "expected_answer": f"Answer {i}",
            "metadata": {"author": f"user{i}", "state": "open" if i % 2 == 0 else "closed"},
        }
        for i in range(n)
    ]


class _PerfectRetriever:
    """Always returns the exact item asked for in top-1."""
    def __init__(self, dataset):
        self._dataset = {item["id"]: item for item in dataset}

    def retrieve(self, query: str, top_k: int = 5) -> list[dict]:
        # Find item by matching question suffix
        for item in self._dataset.values():
            if item["question"] == query:
                return [{"id": item["id"], "content": item["content"], "score": 1.0,
                         "metadata": {"artifact_type": item["artifact_type"],
                                      "author": item["metadata"]["author"]}}]
        return []


class _EmptyRetriever:
    """Returns no results."""
    def retrieve(self, query: str, top_k: int = 5) -> list[dict]:
        return []


# ── knowledge_retention ───────────────────────────────────────────────────────

@pytest.mark.unit
def test_knowledge_retention_perfect_retriever():
    from evaluation.metrics.knowledge_retention import knowledge_retention
    dataset = _make_dataset(5)
    result = knowledge_retention(_PerfectRetriever(dataset), dataset, top_k=1)
    assert result["score"] == 1.0
    assert result["hits"] == 5
    assert result["total"] == 5


@pytest.mark.unit
def test_knowledge_retention_empty_retriever():
    from evaluation.metrics.knowledge_retention import knowledge_retention
    dataset = _make_dataset(5)
    result = knowledge_retention(_EmptyRetriever(), dataset, top_k=5)
    assert result["score"] == 0.0
    assert result["hits"] == 0


@pytest.mark.unit
def test_knowledge_retention_empty_dataset():
    from evaluation.metrics.knowledge_retention import knowledge_retention
    result = knowledge_retention(_EmptyRetriever(), [], top_k=5)
    assert result["score"] == 0.0
    assert result["total"] == 0


# ── attribution_accuracy ──────────────────────────────────────────────────────

@pytest.mark.unit
def test_attribution_accuracy_perfect():
    from evaluation.metrics.attribution_accuracy import attribution_accuracy
    dataset = _make_dataset(4)
    result = attribution_accuracy(_PerfectRetriever(dataset), dataset)
    assert result["score"] == 1.0
    assert result["correct"] == result["total"]


@pytest.mark.unit
def test_attribution_accuracy_no_attributable_items():
    from evaluation.metrics.attribution_accuracy import attribution_accuracy
    dataset = [{"id": "a", "question": "q", "metadata": {}, "content": "c"}]
    result = attribution_accuracy(_EmptyRetriever(), dataset)
    assert result["score"] == 0.0
    assert result["total"] == 0


# ── latency ───────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_latency_returns_expected_fields():
    from evaluation.metrics.latency import measure_latency
    dataset = _make_dataset(3)
    queries = [item["question"] for item in dataset]
    result = measure_latency(_EmptyRetriever(), queries, top_k=5, warmup=0)
    assert "p50_ms" in result
    assert "p95_ms" in result
    assert "p99_ms" in result
    assert result["total_queries"] == 3
    assert result["mean_ms"] >= 0


@pytest.mark.unit
def test_latency_empty_queries():
    from evaluation.metrics.latency import measure_latency
    result = measure_latency(_EmptyRetriever(), [], top_k=5, warmup=0)
    assert result["total_queries"] == 0
    assert result["p50_ms"] == 0.0


# ── conflict_detection ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_conflict_detection_f1_all_correct():
    from evaluation.metrics.conflict_detection import conflict_detection_f1
    preds = [True, True, False, False]
    truth = [True, True, False, False]
    result = conflict_detection_f1(preds, truth)
    assert result["precision"] == 1.0
    assert result["recall"] == 1.0
    assert result["f1"] == 1.0


@pytest.mark.unit
def test_conflict_detection_f1_all_false_negatives():
    from evaluation.metrics.conflict_detection import conflict_detection_f1
    preds = [False, False, False]
    truth = [True, True, True]
    result = conflict_detection_f1(preds, truth)
    assert result["recall"] == 0.0
    assert result["f1"] == 0.0


@pytest.mark.unit
def test_conflict_detection_f1_length_mismatch():
    from evaluation.metrics.conflict_detection import conflict_detection_f1
    with pytest.raises(ValueError):
        conflict_detection_f1([True], [True, False])


# ── role_scoped_retrieval ─────────────────────────────────────────────────────

@pytest.mark.unit
def test_role_scoped_retrieval_empty_dataset():
    from evaluation.metrics.role_scoped_retrieval import role_scoped_retrieval

    class _StubRetriever:
        def retrieve(self, query, top_k=5):
            return []

    result = role_scoped_retrieval(_StubRetriever(), [], role="engineer")
    assert result["score"] == 0.0
    assert result["total_queries"] == 0


@pytest.mark.unit
def test_role_scoped_retrieval_known_role():
    from evaluation.metrics.role_scoped_retrieval import role_scoped_retrieval

    dataset = [
        {"id": "r/c/1", "artifact_type": "commit", "question": "What changed?",
         "content": "c"},
        {"id": "r/i/1", "artifact_type": "issue", "question": "What broke?",
         "content": "c"},
    ]

    class _ScopedRetriever:
        def retrieve(self, query, top_k=5):
            return [{"id": "r/c/1", "content": "c", "score": 1.0,
                     "metadata": {"artifact_type": "commit"}}]

    # Engineer queries commits/PRs — retriever returns commits → 100% in scope
    result = role_scoped_retrieval(_ScopedRetriever(), dataset, role="engineer")
    assert result["score"] == 1.0
