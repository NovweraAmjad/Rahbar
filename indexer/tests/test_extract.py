from __future__ import annotations

import logging
import sys
from types import SimpleNamespace

import pytest

from pipeline.extract import extract


class FakePage:
    def __init__(self, text: str | None) -> None:
        self.text = text

    def extract_text(self) -> str | None:
        return self.text

    def to_image(self, resolution: int) -> SimpleNamespace:
        return SimpleNamespace(original=(resolution, "image"))


class FakePdf:
    def __init__(self, pages: list[FakePage]) -> None:
        self.pages = pages

    def __enter__(self) -> FakePdf:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def test_extract_pdf_pages_with_source_and_page_metadata(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "handbook.pdf"
    pages = [FakePage("First page"), FakePage("Second page")]
    monkeypatch.setattr("pipeline.extract.pdfplumber.open", lambda _path: FakePdf(pages))

    document = extract(str(path), "2026-06", False)

    assert document.source == "handbook.pdf"
    assert document.doc_version == "2026-06"
    assert document.superseded is False
    assert [(page.source, page.page, page.text, page.ocr) for page in document.pages] == [
        ("handbook.pdf", 1, "First page", False),
        ("handbook.pdf", 2, "Second page", False),
    ]


def test_extract_pdf_ocr_for_page_without_text_layer(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "scanned.pdf"
    page = FakePage(None)
    monkeypatch.setattr("pipeline.extract.pdfplumber.open", lambda _path: FakePdf([page]))
    monkeypatch.setitem(
        sys.modules,
        "pytesseract",
        SimpleNamespace(
            image_to_string=lambda image: f"OCR {image[0]}",
            TesseractError=RuntimeError,
        ),
    )

    document = extract(str(path), "unknown", False)

    assert len(document.pages) == 1
    assert document.pages[0].text == "OCR 300"
    assert document.pages[0].ocr is True


@pytest.mark.parametrize("suffix", [".txt", ".md"])
def test_extract_text_file_as_single_page(tmp_path, suffix: str) -> None:
    path = tmp_path / f"policy{suffix}"
    content = "---\ndoc_version: 2026\n---\n# Policy\n\nKeep this text.\n"
    path.write_text(content, encoding="utf-8")

    document = extract(str(path), "2026", True)

    assert document.source == path.name
    assert document.doc_version == "2026"
    assert document.superseded is True
    assert [(page.page, page.text) for page in document.pages] == [(1, content)]


def test_extract_logs_and_returns_empty_document_for_unreadable_text(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "invalid.txt"
    path.write_bytes(b"\xff")

    with caplog.at_level(logging.WARNING):
        document = extract(str(path), "unknown", False)

    assert document.source == path.name
    assert document.pages == []
    assert "Unable to read invalid.txt" in caplog.text


def test_extract_logs_and_returns_empty_document_for_unsupported_file(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "document.html"

    with caplog.at_level(logging.WARNING):
        document = extract(str(path), "unknown", False)

    assert document.pages == []
    assert "Unsupported document type: document.html" in caplog.text


def test_extract_logs_and_returns_empty_document_for_invalid_pdf(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "invalid.pdf"
    path.write_bytes(b"not a PDF")

    with caplog.at_level(logging.WARNING):
        document = extract(str(path), "unknown", False)

    assert document.pages == []
    assert "Unable to extract PDF invalid.pdf" in caplog.text
