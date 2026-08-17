"""Tests for services/ingestion/extractor.py — pipeline Stage 2.

Covers the paths that need no browser or PDF engine: text normalisation, the
code path, and the dispatcher that chooses between them. Getting dispatch
wrong sends content through the wrong extractor and corrupts everything
downstream, since every later stage consumes this output.

URLExtractor is exercised only through its HTML-stripping helper. Driving a
real Playwright browser belongs in an integration test, not here.
"""

from __future__ import annotations

import pytest

from sourcemind.services.ingestion.extractor import (
    CodeExtractor,
    ExtractionResult,
    TextExtractor,
    extract,
)


# ─── TextExtractor ───────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_text_extraction_preserves_content_and_counts_words():
    result = await TextExtractor().extract("hello world from sourcemind")
    assert isinstance(result, ExtractionResult)
    assert result.content == "hello world from sourcemind"
    assert result.metadata["word_count"] == 4


@pytest.mark.unit
@pytest.mark.asyncio
async def test_crlf_is_normalised_to_lf():
    """Windows line endings would otherwise reach the chunker and change
    token boundaries for identical content."""
    result = await TextExtractor().extract("line one\r\nline two\r\n")
    assert "\r" not in result.content
    assert "line one\nline two" in result.content


@pytest.mark.unit
@pytest.mark.asyncio
async def test_excessive_blank_lines_are_collapsed():
    result = await TextExtractor().extract("para one\n\n\n\n\n\npara two")
    assert "\n\n\n\n" not in result.content
    assert "para one" in result.content and "para two" in result.content


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_input_does_not_raise():
    result = await TextExtractor().extract("")
    assert result.content == ""
    assert result.metadata["word_count"] == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unicode_survives_extraction():
    text = "café — naïve — 日本語 — 🎉"
    result = await TextExtractor().extract(text)
    assert result.content == text


# ─── CodeExtractor ───────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize(
    "filename,expected_language",
    [
        ("main.py", "python"),
        ("app.js", "javascript"),
        ("component.tsx", "typescript"),
        ("service.go", "go"),
        ("lib.rs", "rust"),
    ],
)
@pytest.mark.asyncio
async def test_language_is_detected_from_the_file_extension(filename, expected_language):
    result = await CodeExtractor().extract("print('x')", filename)
    assert result.metadata["language"] == expected_language


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unknown_extension_falls_back_to_text():
    result = await CodeExtractor().extract("some content", "notes.xyz")
    assert result.metadata["language"] == "text"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_code_content_is_preserved_verbatim_apart_from_line_endings():
    """Indentation is semantic in several supported languages — it must not
    be normalised away the way prose whitespace is."""
    code = "def f():\n    if x:\n        return 1\n"
    result = await CodeExtractor().extract(code, "f.py")
    assert "    if x:" in result.content
    assert "        return 1" in result.content


@pytest.mark.unit
@pytest.mark.asyncio
async def test_code_extraction_method_is_labelled_honestly():
    """The label must not claim AST parsing that does not happen.

    This reported extraction_method="tree-sitter" while doing nothing but
    map a file extension to a language name, which made a degraded path look
    like the real one in stored metadata.
    """
    result = await CodeExtractor().extract("x = 1", "a.py")
    method = result.metadata.get("extraction_method", "")
    assert "tree-sitter" not in method, (
        f"extraction_method={method!r} claims AST parsing that is not performed"
    )


# ─── dispatcher ──────────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_sends_plain_text_to_the_text_extractor():
    result = await extract(content="just some prose", source_type="text")
    assert result.content_type == "text"
    assert result.content == "just some prose"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_sends_code_to_the_code_extractor():
    result = await extract(
        content="print('hi')", source_type="code", filename="script.py"
    )
    assert result.content_type == "code"
    assert result.metadata["language"] == "python"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_defaults_to_text_for_an_unrecognised_source_type():
    """An unknown source_type must not silently drop the content."""
    result = await extract(content="content here", source_type="something-new")
    assert result.content == "content here"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_without_content_or_url_returns_empty_rather_than_raising():
    result = await extract(source_type="text")
    assert result.content == ""
