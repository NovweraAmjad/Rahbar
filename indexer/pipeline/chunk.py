"""Stage 3: Split page text into structure-aware, overlapping chunks."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Chunk, Document, Page

_TOKEN = re.compile(r"\S+")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_NUMBERED_CLAUSE = re.compile(r"^\s*\d+(?:\.\d+)*\s+\S")
_LOWERCASE_HEADING_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "for",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}
_SENTENCE_END = re.compile(r"""[.!?]["'’”)\]]*$""")


@dataclass(frozen=True)
class _Region:
    text: str
    section: str


def _regions(page: Page) -> list[_Region]:
    regions: list[_Region] = []
    section = ""
    lines: list[str] = []
    page_lines = page.text.splitlines(keepends=True)

    def finish() -> None:
        text = "".join(lines).strip()
        if text:
            regions.append(_Region(text=text, section=section))

    for index, line in enumerate(page_lines):
        stripped_line = line.rstrip("\r\n")
        heading = _HEADING.match(stripped_line)
        pdf_heading = stripped_line.strip()
        next_content = next(
            (
                candidate.strip()
                for candidate in page_lines[index + 1 :]
                if candidate.strip()
            ),
            "",
        )
        heading_words = [
            word for word in pdf_heading.split() if any(char.isalpha() for char in word)
        ]
        title_case = bool(heading_words) and all(
            word[0].isupper() or word.lower() in _LOWERCASE_HEADING_WORDS
            for word in heading_words
        )
        is_pdf_heading = (
            bool(pdf_heading)
            and len(pdf_heading) <= 120
            and title_case
            and not _SENTENCE_END.search(pdf_heading)
            and _NUMBERED_CLAUSE.match(next_content) is not None
        )
        if heading or is_pdf_heading:
            finish()
            lines.clear()
            section = heading.group(1).strip() if heading else pdf_heading
        lines.append(line)
    finish()
    return regions


def _chunk_region(text: str, size: int, overlap: int) -> list[str]:
    matches = list(_TOKEN.finditer(text))
    if not matches:
        return []

    chunks: list[str] = []
    start = 0
    while start < len(matches):
        maximum_end = min(start + size, len(matches))
        end = maximum_end

        if maximum_end < len(matches):
            minimum_end = start + overlap + 1
            natural_ends = []
            for candidate in range(minimum_end, maximum_end + 1):
                current = matches[candidate - 1]
                following = matches[candidate]
                separator = text[current.end() : following.start()]
                if (
                    "\n" in separator
                    or _SENTENCE_END.search(current.group()) is not None
                ):
                    natural_ends.append(candidate)
            if natural_ends:
                end = natural_ends[-1]

        chunks.append(text[matches[start].start() : matches[end - 1].end()])
        if end == len(matches):
            break
        start = end - overlap
    return chunks


def chunk(doc: Document, size: int, overlap: int) -> list[Chunk]:
    """Split pages into bounded whitespace-token chunks with deterministic IDs.

    Overlap is exact within each page and detected heading section. Page and
    section boundaries are never crossed, preserving citation context.
    """
    if size <= 0:
        raise ValueError("chunk size must be a positive integer")
    if overlap < 0:
        raise ValueError("chunk overlap cannot be negative")
    if overlap >= size:
        raise ValueError("chunk overlap must be smaller than chunk size")

    chunks: list[Chunk] = []
    for page in doc.pages:
        if not isinstance(page.text, str):
            continue
        for region in _regions(page):
            for text in _chunk_region(region.text, size, overlap):
                chunk_number = len(chunks) + 1
                chunks.append(
                    Chunk(
                        id=(
                            f"{doc.source}:{doc.doc_version}:"
                            f"{page.page}:{chunk_number}"
                        ),
                        text=text,
                        source=doc.source,
                        section=(
                            region.section
                            or f"Page {page.page} (no section heading detected)"
                        ),
                        page=page.page,
                        doc_version=doc.doc_version,
                        superseded=doc.superseded,
                    )
                )
    return chunks
