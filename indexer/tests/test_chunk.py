from __future__ import annotations

import pytest

from pipeline.chunk import chunk
from pipeline.models import Document, Page


def _document(text: str, **kwargs: object) -> Document:
    return Document(
        source=str(kwargs.get("source", "policy.md")),
        doc_version=str(kwargs.get("doc_version", "2026")),
        superseded=bool(kwargs.get("superseded", False)),
        pages=[
            Page(
                source=str(kwargs.get("source", "policy.md")),
                page=int(kwargs.get("page", 1)),
                text=text,
                ocr=bool(kwargs.get("ocr", False)),
            )
        ],
    )


def test_chunk_empty_document_and_empty_page() -> None:
    document = Document("empty.pdf", "unknown", False)
    document.pages.append(Page(source="empty.pdf", page=1, text=" \n\t "))

    assert chunk(document, size=5, overlap=1) == []


def test_chunk_keeps_short_document_whole() -> None:
    text = "A short policy paragraph with 7 words."

    chunks = chunk(_document(text), size=20, overlap=3)

    assert len(chunks) == 1
    assert chunks[0].text == text


def test_chunk_exact_size_boundary_is_not_split() -> None:
    text = "one two three four five"

    chunks = chunk(_document(text), size=5, overlap=2)

    assert [item.text for item in chunks] == [text]


def test_chunk_splits_long_text_without_natural_boundaries() -> None:
    text = " ".join(f"word{i}" for i in range(13))

    chunks = chunk(_document(text), size=5, overlap=2)

    assert [len(item.text.split()) for item in chunks] == [5, 5, 5, 4]
    assert all(len(item.text.split()) <= 5 for item in chunks)
    assert [item.text.split()[0] for item in chunks] == [
        "word0",
        "word3",
        "word6",
        "word9",
    ]


def test_chunk_overlap_is_exact_between_consecutive_chunks() -> None:
    text = " ".join(f"t{i}" for i in range(11))

    chunks = chunk(_document(text), size=5, overlap=2)

    for previous, following in zip(chunks, chunks[1:]):
        assert previous.text.split()[-2:] == following.text.split()[:2]


def test_chunk_prefers_paragraph_and_sentence_boundaries() -> None:
    text = "One complete sentence. Two complete sentences.\n\nA final paragraph here."

    chunks = chunk(_document(text), size=6, overlap=1)

    assert chunks[0].text == "One complete sentence. Two complete sentences."
    assert chunks[1].text.split()[0] == "sentences."
    assert chunks[1].text.endswith("A final paragraph here.")


def test_chunk_preserves_page_section_and_document_metadata() -> None:
    document = Document(
        source="handbook.pdf",
        doc_version="2026-06",
        superseded=True,
        pages=[
            Page(
                source="handbook.pdf",
                page=4,
                text="# Academic Warning\nA rule applies to students.",
                ocr=True,
            ),
            Page(source="handbook.pdf", page=5, text="Another page."),
        ],
    )

    chunks = chunk(document, size=10, overlap=2)

    assert [(item.source, item.page, item.section) for item in chunks] == [
        ("handbook.pdf", 4, "Academic Warning"),
        ("handbook.pdf", 5, "Page 5 (no section heading detected)"),
    ]
    assert all(item.doc_version == "2026-06" for item in chunks)
    assert all(item.superseded is True for item in chunks)
    assert chunks[0].text.startswith("# Academic Warning")
    assert len({item.id for item in chunks}) == len(chunks)


def test_chunk_detects_plain_pdf_headings_before_numbered_clauses() -> None:
    document = _document(
        "NATIONAL UNIVERSITY\n"
        "Attendance Requirements\n"
        "1.21 Students must maintain attendance.\n"
        "Bachelor's Degree Requirements\n"
        "1.23 The minimum duration is four years.\n",
        source="handbook.pdf",
        page=10,
    )

    chunks = chunk(document, size=50, overlap=5)

    assert [item.section for item in chunks] == [
        "Page 10 (no section heading detected)",
        "Attendance Requirements",
        "Bachelor's Degree Requirements",
    ]
    assert "1.21 Students must maintain attendance." in chunks[1].text
    assert "1.23 The minimum duration is four years." in chunks[2].text


def test_chunk_does_not_infer_a_section_from_lowercase_prose() -> None:
    document = _document(
        "the following requirements apply\n"
        "1.21 Students must maintain attendance.\n",
        source="handbook.pdf",
        page=10,
    )

    chunks = chunk(document, size=50, overlap=5)

    assert len(chunks) == 1
    assert chunks[0].section == "Page 10 (no section heading detected)"


@pytest.mark.parametrize(
    ("size", "overlap"),
    [(0, 0), (-1, 0), (5, -1), (5, 5), (5, 8)],
)
def test_chunk_rejects_invalid_configuration(size: int, overlap: int) -> None:
    with pytest.raises(ValueError):
        chunk(_document("some text"), size=size, overlap=overlap)
