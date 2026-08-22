"""Metrics must score source ARTIFACTS, not internal chunks.

SourceMind extracts several memories from one ingested document — 134 memories
from 20 documents in the last run, so roughly 7 to 1. A baseline that indexes
each document whole returns one result per artifact. Comparing the two without
collapsing to the artifact means measuring the storage layout rather than the
retrieval.

Two different things are being checked here:

  knowledge_retention   already scores per artifact, because the retriever maps
                        every memory back to its ground-truth id and the metric
                        takes a set. These tests pin that down so it cannot
                        regress into per-memory scoring unnoticed.

  role_scoped_retrieval did NOT. It counted one entry per returned memory, so a
                        single artifact represented by three memories weighed
                        three times as much in both halves of the ratio as the
                        same artifact from a single-document baseline.
"""

from __future__ import annotations

import os
import sys
from typing import Any

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))

from evaluation.metrics.knowledge_retention import knowledge_retention  # noqa: E402
from evaluation.metrics.role_scoped_retrieval import role_scoped_retrieval  # noqa: E402

TARGET = "facebook/react/commits/abc123"
OTHER = "rust-lang/rust/commits/def456"


class _FanOutRetriever:
    """Stands in for SourceMind: several memories per source artifact.

    Every entry carries the ground-truth artifact id in ``id``, which is what
    the real retriever does after mapping through its ingestion id map.
    """

    def __init__(self, artifact_ids: list[str], artifact_type: str = "commit"):
        self._artifact_ids = artifact_ids
        self._artifact_type = artifact_type

    def retrieve(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        return [
            {
                "id": artifact_id,
                "content": f"memory {i} of {artifact_id}",
                "score": 1.0 - (i * 0.1),
                "metadata": {"artifact_type": self._artifact_type, "author": "x"},
            }
            for i, artifact_id in enumerate(self._artifact_ids[:top_k])
        ]


def _dataset() -> list[dict[str, Any]]:
    return [
        {
            "id": TARGET,
            "question": "What did commit abc123 change?",
            "artifact_type": "commit",
            "metadata": {"author": "someone"},
        }
    ]


# ─── knowledge_retention ─────────────────────────────────────────────────────

@pytest.mark.unit
def test_a_hit_counts_once_however_many_memories_represent_it():
    """Three memories of the target in the top-5 is one hit, not three.

    The slate is: target, target, other, target, other — the correct artifact
    is represented three times and only one of those needed to rank.
    """
    retriever = _FanOutRetriever([TARGET, TARGET, OTHER, TARGET, OTHER])
    result = knowledge_retention(retriever, _dataset(), top_k=5)

    assert result["hits"] == 1, "the same artifact must not be counted repeatedly"
    assert result["score"] == 1.0


@pytest.mark.unit
def test_one_ranking_memory_is_enough_for_the_artifact_to_count():
    """The artifact appears once, in last place, among four competitors.

    Under per-memory scoring this is the case that gets diluted: four of five
    slots belong to other artifacts. At artifact granularity it is simply a
    hit.
    """
    retriever = _FanOutRetriever([OTHER, OTHER, OTHER, OTHER, TARGET])
    result = knowledge_retention(retriever, _dataset(), top_k=5)

    assert result["hits"] == 1
    assert result["score"] == 1.0


@pytest.mark.unit
def test_the_guard_is_not_vacuous_raw_memory_ids_would_miss():
    """Prove the mapping is what makes it work, by removing it.

    A retriever that returns raw internal memory ids — the shape before the
    ingestion id map existed — scores zero on exactly the slate that scores
    1.0 above. Without this, the tests above would pass for any retriever.
    """

    class _UnmappedRetriever:
        def retrieve(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
            return [
                {"id": f"9f8e7d6c-memory-{i}", "content": "", "score": 0.9,
                 "metadata": {"artifact_type": "commit"}}
                for i in range(top_k)
            ]

    result = knowledge_retention(_UnmappedRetriever(), _dataset(), top_k=5)
    assert result["hits"] == 0, (
        "unmapped memory ids must NOT match a ground-truth artifact id — if "
        "they do, these tests prove nothing"
    )


# ─── role_scoped_retrieval ───────────────────────────────────────────────────

@pytest.mark.unit
def test_role_scope_weighs_each_artifact_once():
    """Five memories spanning two artifacts is a denominator of two.

    Counting results gave 5. A single-document baseline returning the same two
    artifacts gave 2. The two systems were being divided by different numbers
    for identical retrieval.
    """
    retriever = _FanOutRetriever([TARGET, TARGET, TARGET, OTHER, OTHER])
    result = role_scoped_retrieval(retriever, _dataset(), role="engineer", top_k=5)

    assert result["total_results"] == 2, (
        f"expected 2 distinct artifacts, got {result['total_results']} — "
        "results are being counted per memory again"
    )
    assert result["in_scope_results"] == 2
    assert result["score"] == 1.0


@pytest.mark.unit
def test_role_scope_out_of_scope_artifacts_also_count_once():
    """Deduplication must not quietly favour in-scope artifacts.

    Three memories of an out-of-scope artifact and two of an in-scope one is
    one of each, so 0.5 — not 2/5, and not 1/2 by dropping the wrong side.
    """

    class _MixedRetriever:
        def retrieve(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
            rows = [
                (OTHER, "issue"), (OTHER, "issue"), (OTHER, "issue"),
                (TARGET, "commit"), (TARGET, "commit"),
            ]
            return [
                {"id": aid, "content": "", "score": 0.9,
                 "metadata": {"artifact_type": atype}}
                for aid, atype in rows[:top_k]
            ]

    result = role_scoped_retrieval(_MixedRetriever(), _dataset(), role="engineer", top_k=5)

    assert result["total_results"] == 2
    assert result["in_scope_results"] == 1
    assert result["score"] == 0.5
