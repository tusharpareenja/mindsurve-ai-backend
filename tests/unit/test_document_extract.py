"""PDF / Word / plain-text extraction for study AI context."""

from __future__ import annotations

import io

from app.services.document_extract import extract_document_text, is_extractable_document


def test_plain_text_extract() -> None:
    text = extract_document_text(
        filename="notes.txt",
        content_type="text/plain",
        data=b"I feel closest to this idea in nature\nSecond line",
    )
    assert text is not None
    assert "closest to this idea" in text


def test_docx_extract() -> None:
    from docx import Document

    doc = Document()
    doc.add_paragraph("I'd happily spend hours on this idea")
    buf = io.BytesIO()
    doc.save(buf)
    text = extract_document_text(
        filename="brief.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        data=buf.getvalue(),
    )
    assert text is not None
    assert "happily spend hours" in text


def test_pdf_extract_does_not_crash() -> None:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)
    text = extract_document_text(
        filename="copy.pdf",
        content_type="application/pdf",
        data=buf.getvalue(),
    )
    assert is_extractable_document("copy.pdf", "application/pdf")
    assert text is None or isinstance(text, str)


def test_images_are_not_extractable() -> None:
    assert not is_extractable_document("logo.png", "image/png")
    assert (
        extract_document_text(
            filename="logo.png", content_type="image/png", data=b"\x89PNG"
        )
        is None
    )
