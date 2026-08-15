"""Unit tests for Stage 3: Chunker."""

import pytest

from sourcemind.services.ingestion.chunker import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    ChunkResult,
    _count_tokens,
    _split_text_chunks,
)
from sourcemind.services.ingestion.extractor import ExtractionResult


@pytest.mark.unit
def test_empty_content_returns_no_chunks():
    from sourcemind.services.ingestion.extractor import ExtractionResult

    extraction = ExtractionResult(content="", content_type="text", metadata={})

    import asyncio

    from sourcemind.services.ingestion.chunker import chunk

    result = asyncio.run(chunk(extraction))
    assert result == []


@pytest.mark.unit
def test_short_content_single_chunk():
    text = "This is a short piece of content with a single sentence."
    extraction = ExtractionResult(content=text, content_type="text", metadata={})

    import asyncio
    from sourcemind.services.ingestion.chunker import chunk

    chunks = asyncio.run(chunk(extraction))
    assert len(chunks) >= 1
    assert chunks[0].content.strip() != ""


@pytest.mark.unit
def test_chunk_token_count_under_limit():
    # Generate text that should span multiple chunks
    sentence = "The quick brown fox jumps over the lazy dog. "
    text = sentence * 200  # well over 512 tokens
    extraction = ExtractionResult(content=text, content_type="text", metadata={})

    import asyncio
    from sourcemind.services.ingestion.chunker import chunk

    chunks = asyncio.run(chunk(extraction))
    assert len(chunks) > 1
    for c in chunks:
        # Allow slight overage for tables/code blocks but text chunks should be bounded
        if c.chunk_type == "text":
            assert c.token_count <= CHUNK_SIZE + CHUNK_OVERLAP + 50, (
                f"Chunk too large: {c.token_count} tokens"
            )


@pytest.mark.unit
def test_chunk_indices_sequential():
    text = "A sentence. " * 300
    extraction = ExtractionResult(content=text, content_type="text", metadata={})

    import asyncio
    from sourcemind.services.ingestion.chunker import chunk

    chunks = asyncio.run(chunk(extraction))
    for i, c in enumerate(chunks):
        assert c.chunk_index == i
        assert c.total_chunks == len(chunks)


@pytest.mark.unit
def test_table_kept_in_single_chunk():
    table = (
        "| Column A | Column B | Column C |\n"
        "| -------- | -------- | -------- |\n"
        "| Value 1  | Value 2  | Value 3  |\n"
        "| Value 4  | Value 5  | Value 6  |\n"
    )
    text = "Some preamble text.\n\n" + table + "\nSome trailing text."
    extraction = ExtractionResult(content=text, content_type="text", metadata={})

    import asyncio
    from sourcemind.services.ingestion.chunker import chunk

    chunks = asyncio.run(chunk(extraction))
    table_chunks = [c for c in chunks if c.chunk_type == "table"]
    assert len(table_chunks) == 1
    assert "Column A" in table_chunks[0].content
    assert "Column C" in table_chunks[0].content


@pytest.mark.unit
def test_code_block_kept_intact():
    code_block = "```python\ndef hello():\n    print('hello world')\n    return 42\n```"
    text = "Before the code.\n\n" + code_block + "\n\nAfter the code."
    extraction = ExtractionResult(content=text, content_type="text", metadata={})

    import asyncio
    from sourcemind.services.ingestion.chunker import chunk

    chunks = asyncio.run(chunk(extraction))
    code_chunks = [c for c in chunks if c.chunk_type == "code_block"]
    assert len(code_chunks) == 1
    assert "def hello()" in code_chunks[0].content


@pytest.mark.unit
def test_metadata_propagated_to_chunks():
    meta = {"source_url": "https://example.com", "word_count": 100}
    extraction = ExtractionResult(
        content="A sentence. Another sentence.", content_type="text", metadata=meta
    )

    import asyncio
    from sourcemind.services.ingestion.chunker import chunk

    chunks = asyncio.run(chunk(extraction))
    assert len(chunks) >= 1
    assert chunks[0].metadata.get("source_url") == "https://example.com"


@pytest.mark.unit
def test_count_tokens_consistent():
    text = "hello world foo bar"
    count1 = _count_tokens(text)
    count2 = _count_tokens(text)
    assert count1 == count2
    assert count1 > 0
