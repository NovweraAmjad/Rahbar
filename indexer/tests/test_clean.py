from __future__ import annotations

import logging
from typing import cast

from pipeline.clean import clean
from pipeline.models import Document, Page


def test_clean_normalizes_whitespace_and_line_endings() -> None:
    page = Page(
        source="policy.txt",
        page=1,
        text="  Heading  \r\nThis   is\tregular text.\r\n\r\n\r\nNext line.  ",
    )

    clean(Document("policy.txt", "2026", False, [page]))

    assert page.text == "Heading\nThis is regular text.\n\nNext line."


def test_clean_preserves_unicode_punctuation_and_policy_details() -> None:
    page = Page(
        source="policy.md",
        page=1,
        text="Café — 学生; policy § 2.4 (2026-09), GPA ≥ 3.5.",
    )

    clean(Document("policy.md", "2026", False, [page]))

    assert page.text == "Café — 学生; policy § 2.4 (2026-09), GPA ≥ 3.5."


def test_clean_preserves_markdown_table_structure() -> None:
    table = (
        "| Code | Requirement |\n"
        "| --- | --- |\n"
        "| A 1 | At least 3.5 GPA |\n"
        "  Code   Requirement   Effective date  \n"
        "  A1     GPA            2026-09  "
    )
    page = Page(source="rules.md", page=2, text=table)

    clean(Document("rules.md", "2026", False, [page]))

    assert page.text == (
        "| Code | Requirement |\n"
        "| --- | --- |\n"
        "| A 1 | At least 3.5 GPA |\n"
        "Code   Requirement   Effective date\n"
        "A1     GPA            2026-09"
    )


def test_clean_removes_control_characters_and_collapses_blank_lines() -> None:
    page = Page(source="scan.pdf", page=3, text="First\x00 line\n\n\n\nSecond line\x7f")

    clean(Document("scan.pdf", "2026", False, [page]))

    assert page.text == "First line\n\nSecond line"


def test_clean_handles_empty_and_malformed_text(caplog) -> None:
    empty_page = Page(source="empty.txt", page=1, text="")
    malformed_page = Page(source="malformed.txt", page=4, text=cast(str, None))
    document = Document("source", "unknown", True, [empty_page, malformed_page])

    with caplog.at_level(logging.WARNING):
        result = clean(document)

    assert result is document
    assert empty_page.text == ""
    assert malformed_page.text == ""
    assert "Non-text content in malformed.txt page 4" in caplog.text


def test_clean_preserves_document_and_page_metadata() -> None:
    page = Page(source="handbook.pdf", page=7, text="  Section  3.1  ", ocr=True)
    document = Document(
        source="handbook.pdf",
        doc_version="2026-06",
        superseded=True,
        pages=[page],
    )

    result = clean(document)

    assert result is document
    assert result.source == "handbook.pdf"
    assert result.doc_version == "2026-06"
    assert result.superseded is True
    assert result.pages[0] is page
    assert (page.source, page.page, page.ocr) == ("handbook.pdf", 7, True)
    assert page.text == "Section 3.1"
