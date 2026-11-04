"""Stage 3: Split page text into structure-aware, overlapping chunks."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Chunk, Document, Page

_TOKEN = re.compile(r"\S+")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_SENTENCE_END = re.compile(r"""[.!?]["'’”)\]]*$""")


@dataclass(frozen=True)
class _Region:
    text: str
    section: str


def _regions(page: Page) -> list[_Region]:
    regions: list[_Region] = []
    section = ""
    lines: list[str] = []

    def finish() -> None:
        text = "".join(lines).strip()
        if text:
            regions.append(_Region(text=text, section=section))

    for line in page.text.splitlines(keepends=True):
        heading = _HEADING.match(line.rstrip("\r\n"))
        if heading:
            finish()
            lines.clear()
            section = heading.group(1).strip()
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

    Overlap is exact within each page and Markdown heading section. Page and
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
                        section=region.section,
                        page=page.page,
                        doc_version=doc.doc_version,
                        superseded=doc.superseded,
                    )
                )
    return chunks
