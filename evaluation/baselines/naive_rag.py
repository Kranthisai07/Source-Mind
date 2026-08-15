"""Naive RAG baseline — Chroma vector store + OpenAI embeddings.

Provides a simple retrieval baseline to compare against SourceMind's
hybrid search (pgvector + BM25 + RRF). Uses chromadb in-memory mode so
no external services are required.
"""

from __future__ import annotations

import os
from typing import Any

try:
    import chromadb
    from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction
    CHROMADB_AVAILABLE = True
except ImportError:
    CHROMADB_AVAILABLE = False


class NaiveRAGBaseline:
    """Naive dense retrieval baseline using Chroma + OpenAI text-embedding-3-large.

    Args:
        collection_name: Chroma collection name (default ``"naive_rag"``).
        openai_api_key: OpenAI API key; falls back to ``OPENAI_API_KEY`` env var.
        embedding_model: OpenAI embedding model name.
    """

    def __init__(
        self,
        collection_name: str = "naive_rag",
        openai_api_key: str | None = None,
        embedding_model: str = "text-embedding-3-large",
    ) -> None:
        if not CHROMADB_AVAILABLE:
            raise ImportError(
                "chromadb is required for NaiveRAGBaseline. "
                "Install with: pip install chromadb"
            )
        api_key = openai_api_key or os.environ.get("OPENAI_API_KEY", "")
        embed_fn = OpenAIEmbeddingFunction(
            api_key=api_key,
            model_name=embedding_model,
        )
        self._client = chromadb.Client()
        self._collection = self._client.create_collection(
            name=collection_name,
            embedding_function=embed_fn,
            get_or_create=True,
        )

    def index(self, items: list[dict[str, Any]]) -> None:
        """Add ground-truth items to the Chroma collection.

        Args:
            items: List of dicts with at least ``id``, ``content`` keys.
        """
        if not items:
            return
        self._collection.add(
            ids=[item["id"] for item in items],
            documents=[item["content"] for item in items],
            metadatas=[
                {
                    "repo": item.get("repo", ""),
                    "artifact_type": item.get("artifact_type", ""),
                    "source_id": item.get("source_id", ""),
                }
                for item in items
            ],
        )

    def retrieve(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Retrieve the top-k most relevant documents for a query.

        Args:
            query: Natural language query string.
            top_k: Number of results to return.

        Returns:
            List of result dicts with ``id``, ``content``, ``score``,
            and ``metadata`` keys.
        """
        results = self._collection.query(
            query_texts=[query],
            n_results=top_k,
            include=["documents", "distances", "metadatas"],
        )
        output = []
        ids = (results.get("ids") or [[]])[0]
        docs = (results.get("documents") or [[]])[0]
        distances = (results.get("distances") or [[]])[0]
        metadatas = (results.get("metadatas") or [[]])[0]

        for doc_id, doc, dist, meta in zip(ids, docs, distances, metadatas):
            # Convert L2 distance to a similarity score in [0, 1]
            score = max(0.0, 1.0 - dist)
            output.append({
                "id": doc_id,
                "content": doc,
                "score": score,
                "metadata": meta,
            })
        return output

    def clear(self) -> None:
        """Remove all documents from the collection."""
        self._collection.delete(where={"repo": {"$ne": ""}})
