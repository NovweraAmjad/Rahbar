"""Stage 1: Extract text from PDF and text-based documents."""

from __future__ import annotations

import logging
from pathlib import Path

import pdfplumber
from pdfminer.pdfparser import PDFException
from pdfplumber.utils.exceptions import PdfminerException

from .models import Document, Page

logger = logging.getLogger(__name__)


def _extract_pdf(path: Path, source: str) -> list[Page]:
    pages: list[Page] = []
    with pdfplumber.open(path) as pdf:
        for page_number, pdf_page in enumerate(pdf.pages, start=1):
            text = pdf_page.extract_text() or ""
            ocr = False
            if not text.strip():
                try:
                    import pytesseract
                except ImportError as exc:
                    logger.warning("OCR unavailable for %s page %d: %s", source, page_number, exc)
                else:
                    try:
                        image = pdf_page.to_image(resolution=300).original
                        text = pytesseract.image_to_string(image)
                        ocr = True
                    except (
                        OSError,
                        PDFException,
                        PdfminerException,
                        RuntimeError,
                        ValueError,
                        pytesseract.TesseractError,
                    ) as exc:
                        logger.warning(
                            "OCR failed for %s page %d: %s", source, page_number, exc
                        )
            pages.append(Page(source=source, page=page_number, text=text, ocr=ocr))
    return pages


def extract(path: str, doc_version: str, superseded: bool) -> Document:
    """Extract a document into pages, retaining its source and version metadata.

    PDF pages without a text layer are OCR'd when pytesseract and its Tesseract
    runtime are available. Unsupported or unreadable files are logged and returned
    with no pages.
    """
    file_path = Path(path)
    source = file_path.name
    document = Document(source=source, doc_version=doc_version, superseded=superseded)

    if file_path.suffix.lower() in {".txt", ".md"}:
        try:
            text = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            logger.warning("Unable to read %s: %s", source, exc)
            return document
        document.pages.append(Page(source=source, page=1, text=text))
        return document

    if file_path.suffix.lower() != ".pdf":
        logger.warning("Unsupported document type: %s", source)
        return document

    try:
        document.pages = _extract_pdf(file_path, source)
    except (OSError, PDFException, PdfminerException, ValueError) as exc:
        logger.warning("Unable to extract PDF %s: %s", source, exc)
    return document
