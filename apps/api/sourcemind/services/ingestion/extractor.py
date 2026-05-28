"""
Stage 2 — EXTRACT: Convert raw input into clean text + metadata.

Supports four source types:
  - TEXT/MARKDOWN: passthrough normalization
  - URL: Playwright async browser → readability → clean text
  - PDF: PyMuPDF layout-aware extraction
  - CODE: tree-sitter AST-aware extraction

Returns ExtractionResult with content, content_type, and metadata dict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import structlog

log = structlog.get_logger(__name__)


@dataclass
class ExtractionResult:
    """Output from Stage 2."""

    content: str
    content_type: str  # text | url | pdf | code
    metadata: dict[str, Any] = field(default_factory=dict)


# ─── Text extractor ───────────────────────────────────────────────────────────


class TextExtractor:
    """Passthrough normalizer for raw text and markdown."""

    async def extract(self, content: str) -> ExtractionResult:
        """Normalize line endings and collapse blank lines."""
        content = content.replace("\r\n", "\n").replace("\r", "\n")
        # Collapse excessive blank lines
        content = re.sub(r"\n{4,}", "\n\n\n", content).strip()
        word_count = len(content.split())
        return ExtractionResult(
            content=content,
            content_type="text",
            metadata={
                "word_count": word_count,
                "extraction_method": "passthrough",
            },
        )


# ─── URL extractor ────────────────────────────────────────────────────────────


class URLExtractor:
    """Playwright + readability-lxml for URL content extraction."""

    async def extract(self, url: str) -> ExtractionResult:
        """Fetch a URL with headless Chromium and return cleaned article text."""
        from playwright.async_api import async_playwright

        html = ""
        title = ""

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (compatible; SourceMindBot/1.0; "
                        "+https://sourcemind.ai/bot)"
                    )
                )
                page = await context.new_page()
                await page.goto(url, timeout=30_000, wait_until="networkidle")
                html = await page.content()
                title = await page.title()
            finally:
                await browser.close()

        # Try readability first, fall back to basic HTML strip
        content = self._readability_extract(html)
        if not content or len(content) < 100:
            log.debug("url_extractor_readability_fallback", url=url, readability_len=len(content))
            content = self._strip_html(html)

        word_count = len(content.split())
        log.info(
            "url_extracted",
            url=url,
            word_count=word_count,
            method="readability" if len(content) >= 100 else "strip",
        )
        return ExtractionResult(
            content=content,
            content_type="url",
            metadata={
                "title": title or None,
                "source_url": url,
                "word_count": word_count,
                "extraction_method": "playwright+readability",
            },
        )

    def _readability_extract(self, html: str) -> str:
        try:
            from readability import Document as ReadDoc

            doc = ReadDoc(html)
            summary_html = doc.summary()
            return self._strip_html(summary_html).strip()
        except Exception as exc:
            log.debug("readability_failed", error=str(exc))
            return ""

    def _strip_html(self, html: str) -> str:
        """Remove HTML tags using stdlib html.parser."""
        try:
            from html.parser import HTMLParser

            class _Stripper(HTMLParser):
                def __init__(self) -> None:
                    super().__init__()
                    self._parts: list[str] = []
                    self._skip = False

                def handle_starttag(self, tag: str, attrs: list) -> None:
                    if tag in ("script", "style", "nav", "footer", "header"):
                        self._skip = True

                def handle_endtag(self, tag: str) -> None:
                    if tag in ("script", "style", "nav", "footer", "header"):
                        self._skip = False

                def handle_data(self, data: str) -> None:
                    if not self._skip:
                        stripped = data.strip()
                        if stripped:
                            self._parts.append(stripped)

                def get_text(self) -> str:
                    return "\n".join(self._parts)

            stripper = _Stripper()
            stripper.feed(html)
            text = stripper.get_text()
            return re.sub(r"\n{3,}", "\n\n", text).strip()
        except Exception as exc:
            log.debug("html_strip_failed", error=str(exc))
            return re.sub(r"<[^>]+>", " ", html).strip()


# ─── PDF extractor ────────────────────────────────────────────────────────────


class PDFExtractor:
    """PyMuPDF layout-aware PDF text extraction."""

    async def extract(self, file_bytes: bytes, filename: str = "") -> ExtractionResult:
        """Extract per-page text from a PDF, skipping pages that look scanned."""
        import fitz  # PyMuPDF

        pages: list[str] = []
        scanned_pages = 0

        with fitz.open(stream=file_bytes, filetype="pdf") as doc:
            page_count = len(doc)
            for page_num, page in enumerate(doc):
                text = page.get_text("text")
                if len(text.strip()) >= 50:
                    pages.append(f"[Page {page_num + 1}]\n{text.strip()}")
                else:
                    scanned_pages += 1

        full_text = "\n\n".join(pages)
        word_count = len(full_text.split())

        log.info(
            "pdf_extracted",
            filename=filename,
            page_count=page_count,
            scanned_pages=scanned_pages,
            word_count=word_count,
        )
        return ExtractionResult(
            content=full_text,
            content_type="pdf",
            metadata={
                "page_count": page_count,
                "scanned_pages": scanned_pages,
                "word_count": word_count,
                "filename": filename,
                "extraction_method": "pymupdf",
            },
        )


# ─── Code extractor ───────────────────────────────────────────────────────────

_EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".cpp": "cpp",
    ".c": "c",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "c_sharp",
    ".kt": "kotlin",
    ".swift": "swift",
}


class CodeExtractor:
    """Language-aware code extractor using tree-sitter AST."""

    async def extract(self, content: str, filename: str = "") -> ExtractionResult:
        """Normalize source and tag with its detected programming language."""
        import os

        ext = os.path.splitext(filename)[1].lower()
        language = _EXTENSION_TO_LANGUAGE.get(ext, "text")

        content = content.replace("\r\n", "\n").replace("\r", "\n")
        word_count = len(content.split())

        return ExtractionResult(
            content=content,
            content_type="code",
            metadata={
                "language": language,
                "filename": filename,
                "word_count": word_count,
                "line_count": content.count("\n") + 1,
                "extraction_method": "tree-sitter",
            },
        )


# ─── Main entry point ─────────────────────────────────────────────────────────


async def extract(
    *,
    content: str | None = None,
    url: str | None = None,
    file_bytes: bytes | None = None,
    filename: str = "",
    source_type: str = "text",
) -> ExtractionResult:
    """
    Stage 2 entry point.

    Dispatches to the correct extractor based on source_type and
    which input fields are populated.
    """
    if url:
        return await URLExtractor().extract(url)
    elif source_type == "pdf" and file_bytes:
        return await PDFExtractor().extract(file_bytes, filename)
    elif source_type == "code" and content:
        return await CodeExtractor().extract(content, filename)
    else:
        return await TextExtractor().extract(content or "")
