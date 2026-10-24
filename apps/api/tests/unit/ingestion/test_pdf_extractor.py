"""PDF text extraction through pypdfium2 (D-054).

PyMuPDF (AGPL-3.0) was replaced because it is declared and used on the ingestion path of a
service that other people will use over a network. PDF extraction had no tests before the
swap, so these pin the behaviour the rest of the pipeline relies on: page markers, the
scanned-page rule (a page with fewer than 50 characters of text is skipped and counted),
Unix newlines, the metadata keys and the error type for bad input.

The PDFs are built in the test from a few lines of PDF syntax, so no fixture file or second
PDF library is needed.
"""

from __future__ import annotations

import pypdfium2 as pdfium
import pytest

from sourcemind.services.ingestion.extractor import PDFExtractor, extract


def _pdf(pages: list[list[str]]) -> bytes:
    """Build a small valid PDF. Each page is a list of text lines set in 12 pt Helvetica."""
    count = len(pages)
    first_page_obj = 4
    kids = " ".join(f"{first_page_obj + 2 * i} 0 R" for i in range(count))
    bodies = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {count} >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for index, lines in enumerate(pages):
        content_no = first_page_obj + 2 * index + 1
        ops = ["BT", "/F1 12 Tf", "72 720 Td", "16 TL"]
        for line in lines:
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"({escaped}) Tj")
            ops.append("T*")
        ops.append("ET")
        stream = "\n".join(ops)
        bodies.append(
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Contents {content_no} 0 R /Resources << /Font << /F1 3 0 R >> >> >>"
        )
        bodies.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")

    out = "%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(bodies, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(bodies) + 1}\n0000000000 65535 f \n"
    out += "".join(f"{offset:010d} 00000 n \n" for offset in offsets)
    out += f"trailer\n<< /Size {len(bodies) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    return out.encode("latin-1")


LONG_1 = "SourceMind keeps the decisions behind a project,"
LONG_2 = "who made each call, and why Redis was chosen."


@pytest.mark.unit
@pytest.mark.asyncio
async def test_text_pages_are_extracted_with_page_markers_and_unix_newlines():
    result = await PDFExtractor().extract(_pdf([[LONG_1, LONG_2]]), "notes.pdf")

    assert result.content_type == "pdf"
    assert result.content.startswith("[Page 1]\n")
    assert LONG_1 in result.content and LONG_2 in result.content
    assert "\r" not in result.content


@pytest.mark.unit
@pytest.mark.asyncio
async def test_lines_of_a_page_stay_on_separate_lines():
    result = await PDFExtractor().extract(_pdf([[LONG_1, LONG_2]]))

    assert f"{LONG_1}\n{LONG_2}" in result.content


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_page_with_almost_no_text_is_skipped_and_counted_as_scanned():
    result = await PDFExtractor().extract(_pdf([[LONG_1, LONG_2], ["scan"]]), "mixed.pdf")

    assert "[Page 1]" in result.content
    assert "[Page 2]" not in result.content
    assert result.metadata["page_count"] == 2
    assert result.metadata["scanned_pages"] == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pages_keep_their_original_numbers_when_an_earlier_page_is_skipped():
    result = await PDFExtractor().extract(_pdf([["scan"], [LONG_1, LONG_2]]))

    assert "[Page 2]" in result.content
    assert "[Page 1]" not in result.content


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_pdf_with_no_extractable_text_gives_empty_content():
    result = await PDFExtractor().extract(_pdf([["a"], ["b"]]))

    assert result.content == ""
    assert result.metadata["scanned_pages"] == 2
    assert result.metadata["word_count"] == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_metadata_names_the_engine_and_the_file():
    result = await PDFExtractor().extract(_pdf([[LONG_1, LONG_2]]), "notes.pdf")

    assert result.metadata["extraction_method"] == "pypdfium2"
    assert result.metadata["filename"] == "notes.pdf"
    assert result.metadata["word_count"] == len(result.content.split())


@pytest.mark.unit
@pytest.mark.asyncio
async def test_bytes_that_are_not_a_pdf_raise_instead_of_returning_empty_content():
    """The worker records the failure; returning "" would look like a scanned document."""
    with pytest.raises(pdfium.PdfiumError):
        await PDFExtractor().extract(b"this is not a pdf", "bad.pdf")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatcher_sends_pdf_source_type_to_the_pdf_extractor():
    result = await extract(
        file_bytes=_pdf([[LONG_1, LONG_2]]), filename="notes.pdf", source_type="pdf"
    )

    assert result.content_type == "pdf"
    assert LONG_1 in result.content
