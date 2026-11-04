"""Stage 2: Clean extracted text without removing document structure."""

from __future__ import annotations

import logging
import re
import unicodedata

from .models import Document

logger = logging.getLogger(__name__)

_LINE_BREAKS = re.compile(r"\r\n?|\v|\f|\x85|\u2028|\u2029")
_ALIGNED_COLUMNS = re.compile(r"\S+ {2,}\S+")


def _clean_text(text: str) -> str:
    text = _LINE_BREAKS.sub("\n", text).replace("\u00a0", " ").expandtabs(4)
    text = "".join(
        character
        for character in text
        if character in "\n\t" or unicodedata.category(character) != "Cc"
    )
    lines = text.split("\n")
    cleaned_lines: list[str] = []

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            if cleaned_lines and cleaned_lines[-1] != "":
                cleaned_lines.append("")
            continue

        has_aligned_columns = bool(_ALIGNED_COLUMNS.search(line))
        adjacent_aligned_row = (
            index > 0 and bool(_ALIGNED_COLUMNS.search(lines[index - 1]))
        ) or (
            index + 1 < len(lines) and bool(_ALIGNED_COLUMNS.search(lines[index + 1]))
        )
        is_table_row = "|" in line or (has_aligned_columns and adjacent_aligned_row)
        if not is_table_row:
            stripped = re.sub(r"\s+", " ", stripped)
        cleaned_lines.append(stripped)

    while cleaned_lines and not cleaned_lines[-1]:
        cleaned_lines.pop()
    return "\n".join(cleaned_lines)


def clean(doc: Document) -> Document:
    """Normalize page text while preserving line structure and metadata."""
    for page in doc.pages:
        if not isinstance(page.text, str):
            logger.warning(
                "Non-text content in %s page %d; replacing it with empty text",
                page.source,
                page.page,
            )
            page.text = ""
        else:
            page.text = _clean_text(page.text)
    return doc
