"""
Stage 3 — CHUNK: Split extracted text into overlapping chunks.

Text/URL/PDF: 512-token target, 64-token overlap, sentence boundaries.
Code: function/class boundaries via tree-sitter AST.

Never splits:
  - Mid-sentence (text mode)
  - Mid-table (markdown tables kept whole)
  - Mid-code-block (fenced ``` blocks kept whole)
  - Mid-function (code mode)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import structlog

log = structlog.get_logger(__name__)

CHUNK_SIZE = 512       # target tokens per chunk
CHUNK_OVERLAP = 64     # overlap tokens carried to next chunk
MAX_TABLE_TOKENS = 1024  # hard cap for table chunks


@dataclass
class ChunkResult:
    """One chunk produced by Stage 3, ready for fact extraction."""

    content: str
    token_count: int
    chunk_index: int
    total_chunks: int
    chunk_type: str  # text | table | code_block | function | class | module
    metadata: dict[str, Any] = field(default_factory=dict)


# ─── Tokenizer ────────────────────────────────────────────────────────────────


def _get_encoder():  # type: ignore[return]
    import tiktoken
    return tiktoken.get_encoding("cl100k_base")


def _count_tokens(text: str) -> int:
    enc = _get_encoder()
    return len(enc.encode(text))


# ─── Segment splitter ─────────────────────────────────────────────────────────


_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
_TABLE_RE = re.compile(r"(?:^\|.+\|\n)+", re.MULTILINE)


def _split_into_segments(text: str) -> list[tuple[str, str]]:
    """
    Split markdown text into (type, content) segments.
    Types: 'code_block', 'table', 'text'
    """
    segments: list[tuple[str, str]] = []
    specials: list[tuple[str, int, int, str]] = []

    for m in _CODE_BLOCK_RE.finditer(text):
        specials.append(("code_block", m.start(), m.end(), m.group()))
    for m in _TABLE_RE.finditer(text):
        # Don't include if already inside a code block
        specials.append(("table", m.start(), m.end(), m.group()))

    # Sort by position, remove overlaps (code_blocks take priority)
    specials.sort(key=lambda x: x[1])
    non_overlapping: list[tuple[str, int, int, str]] = []
    last_end = 0
    for seg_type, start, end, content in specials:
        if start >= last_end:
            non_overlapping.append((seg_type, start, end, content))
            last_end = end

    pos = 0
    for seg_type, start, end, content in non_overlapping:
        if start > pos:
            segments.append(("text", text[pos:start]))
        segments.append((seg_type, content))
        pos = end
    if pos < len(text):
        segments.append(("text", text[pos:]))

    return [(t, c) for t, c in segments if c.strip()]


# ─── Text chunker ─────────────────────────────────────────────────────────────


def _split_text_chunks(text: str, source_metadata: dict[str, Any]) -> list[ChunkResult]:
    """
    Chunk plain text preserving sentence boundaries.

    Algorithm:
      1. Split text into segments (tables, code blocks, regular text)
      2. For regular text: use spaCy sentence boundaries
      3. Accumulate sentences until approaching CHUNK_SIZE
      4. On overflow: flush chunk, carry last CHUNK_OVERLAP tokens as overlap
    """
    enc = _get_encoder()

    # Try spaCy sentencizer; fall back to regex if unavailable
    nlp = None
    try:
        import spacy
        nlp = spacy.load("en_core_web_sm", disable=["ner", "lemmatizer"])
    except Exception:
        pass

    def _sentencize(s: str) -> list[str]:
        if nlp is not None:
            doc = nlp(s)
            return [sent.text.strip() for sent in doc.sents if sent.text.strip()]
        return [x.strip() for x in re.split(r"(?<=[.!?])\s+", s) if x.strip()]

    chunks: list[ChunkResult] = []
    chunk_index = 0

    def _flush(token_buf: list[int], text_buf: list[str], ctype: str = "text") -> None:
        nonlocal chunk_index
        content = " ".join(text_buf).strip()
        if not content:
            return
        tc = len(token_buf)
        chunks.append(
            ChunkResult(
                content=content,
                token_count=tc,
                chunk_index=chunk_index,
                total_chunks=0,
                chunk_type=ctype,
                metadata=dict(source_metadata),
            )
        )
        chunk_index += 1

    token_buf: list[int] = []
    text_buf: list[str] = []

    for seg_type, seg_content in _split_into_segments(text):
        if seg_type in ("table", "code_block"):
            # Flush pending text first
            _flush(token_buf, text_buf)
            token_buf = []
            text_buf = []

            tc = _count_tokens(seg_content)
            chunks.append(
                ChunkResult(
                    content=seg_content,
                    token_count=tc,
                    chunk_index=chunk_index,
                    total_chunks=0,
                    chunk_type=seg_type,
                    metadata=dict(source_metadata),
                )
            )
            chunk_index += 1
            continue

        # Regular text: accumulate sentences
        sentences = _sentencize(seg_content)
        for sentence in sentences:
            sent_toks = enc.encode(sentence)

            if len(token_buf) + len(sent_toks) > CHUNK_SIZE and token_buf:
                _flush(token_buf, text_buf)

                # Build overlap: walk backward through text_buf collecting tokens
                overlap_text: list[str] = []
                overlap_count = 0
                for prev in reversed(text_buf):
                    prev_toks = enc.encode(prev)
                    if overlap_count + len(prev_toks) > CHUNK_OVERLAP:
                        break
                    overlap_text.insert(0, prev)
                    overlap_count += len(prev_toks)

                text_buf = overlap_text
                token_buf = enc.encode(" ".join(text_buf))

            text_buf.append(sentence)
            token_buf.extend(sent_toks)

    _flush(token_buf, text_buf)

    total = len(chunks)
    for c in chunks:
        c.total_chunks = total

    log.debug(
        "text_chunked",
        chunk_count=total,
        avg_tokens=int(sum(c.token_count for c in chunks) / max(total, 1)),
    )
    return chunks


# ─── Code chunker ─────────────────────────────────────────────────────────────


def _extract_imports(content: str, root: Any, language: str) -> str:  # pragma: no cover
    # Reachable only where tree_sitter_languages is installed, which is
    # gated to python_version < '3.13' in pyproject.toml because no newer
    # wheels exist. It is therefore live in the py3.12 production image but
    # cannot execute — or be tested — on the 3.14 dev interpreter.
    # NOTE: that means this path ships untested. Covering it requires
    # running the suite inside the production image.

    """Extract import block from AST root node."""
    import_node_types: dict[str, list[str]] = {
        "python": ["import_statement", "import_from_statement"],
        "javascript": ["import_declaration"],
        "typescript": ["import_declaration"],
        "go": ["import_declaration"],
        "rust": ["use_declaration"],
    }
    types_to_find = import_node_types.get(language, [])
    lines: list[str] = []
    for child in root.children:
        if child.type in types_to_find:
            lines.append(content[child.start_byte:child.end_byte])
    return "\n".join(lines)


def _extract_code_units(content: str, root: Any, language: str) -> list[tuple[str, str, str]]:  # pragma: no cover
    """
    Extract (unit_type, unit_name, unit_content) from AST.

    unit_type: 'function' | 'class'
    Does not recurse into function/class bodies.
    """
    function_types: dict[str, list[str]] = {
        "python": ["function_definition", "async_function_definition"],
        "javascript": ["function_declaration", "method_definition"],
        "typescript": ["function_declaration", "method_definition"],
        "go": ["function_declaration", "method_declaration"],
        "rust": ["function_item"],
    }
    class_types: dict[str, list[str]] = {
        "python": ["class_definition"],
        "javascript": ["class_declaration"],
        "typescript": ["class_declaration"],
        "go": ["type_spec"],
    }

    func_types = function_types.get(language, ["function_definition"])
    cls_types = class_types.get(language, ["class_definition"])

    units: list[tuple[str, str, str]] = []

    def walk(node: Any) -> None:
        for child in node.children:
            if child.type in func_types or child.type in cls_types:
                unit_type = "function" if child.type in func_types else "class"
                name = ""
                for grandchild in child.children:
                    if grandchild.type == "identifier":
                        name = content[grandchild.start_byte:grandchild.end_byte]
                        break
                units.append((unit_type, name, content[child.start_byte:child.end_byte]))
            else:
                walk(child)

    walk(root)
    return units


def _split_code_chunks(
    content: str, language: str, source_metadata: dict[str, Any]
) -> list[ChunkResult]:
    """AST-based code chunking via tree-sitter."""
    try:
        import tree_sitter_languages
        from tree_sitter import Parser

        lang = tree_sitter_languages.get_language(language)
        # tree-sitter ≥0.22 takes the Language directly; older builds need
        # the deprecated set_language. Try the new API first, fall back.
        try:
            parser = Parser(lang)
        except TypeError:
            parser = Parser()
            parser.set_language(lang)  # type: ignore[attr-defined]
        tree = parser.parse(bytes(content, "utf8"))
        root = tree.root_node

        imports = _extract_imports(content, root, language)
        units = _extract_code_units(content, root, language)

        if not units:
            log.debug("code_no_units_found", language=language)
            return _split_text_chunks(content, source_metadata)

        chunks: list[ChunkResult] = []
        for idx, (unit_type, name, unit_content) in enumerate(units):
            full_content = f"{imports}\n\n{unit_content}" if imports else unit_content
            tc = _count_tokens(full_content)
            chunks.append(
                ChunkResult(
                    content=full_content,
                    token_count=tc,
                    chunk_index=idx,
                    total_chunks=len(units),
                    chunk_type=unit_type,
                    metadata={
                        **source_metadata,
                        "function_name": name if unit_type == "function" else None,
                        "class_name": name if unit_type == "class" else None,
                        "oversized": tc > 1024,
                    },
                )
            )

        total = len(chunks)
        for c in chunks:
            c.total_chunks = total

        log.debug("code_chunked", language=language, chunk_count=total)
        return chunks

    except Exception as exc:
        log.warning("tree_sitter_failed", language=language, error=str(exc))
        return _split_text_chunks(content, source_metadata)


# ─── Main entry point ─────────────────────────────────────────────────────────


async def chunk(extraction_result: Any) -> list[ChunkResult]:
    """
    Stage 3 entry point.

    Dispatches to code chunker or text chunker based on content_type.
    """
    content = extraction_result.content
    content_type = extraction_result.content_type
    metadata = extraction_result.metadata

    if not content.strip():
        return []

    if content_type == "code":
        language = metadata.get("language", "text")
        if language == "text":
            return _split_text_chunks(content, metadata)
        return _split_code_chunks(content, language, metadata)
    else:
        return _split_text_chunks(content, metadata)
