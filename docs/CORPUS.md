# Corpus

## Inventory and reproduction

The corpus uses public FAST-NUCES PDFs. Seven PDFs were fetched and checked on
2026-11-04; together they contain 279 PDF pages. Their original source URLs,
byte sizes, SHA-256 checksums, version labels, and filenames are recorded in
[MANIFEST.md](../corpus/MANIFEST.md). `corpus/raw/` is gitignored, so third-party
PDFs are not committed.

From the repository root, run `python corpus/fetch.py` to fetch the documents
listed in `corpus/sources.yaml`, then `python corpus/fetch.py --verify` to
compare the local bytes with the manifest. The fetcher URL-encodes unsafe URL
characters, converts Google Drive viewer URLs to direct-download URLs, skips
duplicate normalized URLs, and waits two seconds after each download request.
The source list does not include any login-protected material.

| Document | PDF pages | Corpus role |
| --- | ---: | --- |
| Undergraduate Academic Rules (Aug 2023) | 26 | Earlier policy version |
| Academic Rules Handbook (Jun 2026) | 58 | Current policy version and exact clauses |
| Prospectus 2026 - Academic Rules and Regulations | 5 | Compact policy rendering and grading terms |
| Student Handbook (Jun 2026) | 64 | Student-facing policy, including Generative AI |
| Annual Report 2019-20 | 33 | Earlier report edition |
| Annual Report 2023-24 | 85 | Tables with campus and faculty data |
| Faculty Entrepreneurship and Technical Start-ups Policy | 8 | Image-only scanned PDF |
| **Total** | **279** | **7 public documents** |

Page counts were read from the downloaded PDFs with `pypdf`. The manifest's
`--verify` check passed for all seven files.

## Required properties

| # | Required property | Status | Evidence and remaining work |
| --- | --- | --- | --- |
| 1 | At least 6 public/owned documents and 40 pages | **Verified** | Seven public PDFs, 279 pages total. Source URLs and verified SHA-256 values are in the manifest. |
| 2 | Two different versions of the same policy | **Partially verified** | The public FAST-NUCES Undergraduate Academic Rules (Aug 2023) and Academic Rules Handbook (Jun 2026) are listed as the earlier/current pair. `sources.yaml` marks `2023-08` as superseded and `2026-06` as current. The 2023 PDF has extractable text only on pages 1 and 26; substantive clause-by-clause comparison remains limited, although the golden set and evaluator contain version-sensitive cases. |
| 3 | At least one scanned, image-only PDF | **PDF property verified; OCR incomplete** | All 8 pages of the Faculty Entrepreneurship and Technical Start-ups Policy have zero extracted text characters, one embedded page image each, and no PDF font resources. Tesseract is not installed in the current WSL environment, so OCR output and the document's text content have not been verified. |
| 4 | A table carrying real information | **Verified** | Annual Report 2023-24, PDF page 13, contains a faculty summary by campus with professor/associate/assistant professor, lecturer, instructor and lab-engineer counts; its total row reports 735. Page 14 continues department-level faculty counts. |
| 5 | At least one document with long definitions | **Partially verified** | Student Handbook 2026, PDF pages 48-53, contains Section XII, “Policy on Generative AI Based Tools,” with an introduction and numbered policy subsections. The section is a candidate long-span case, but a specific definition exceeding the chunk target has not yet been confirmed; measure a definition passage and its chunk boundary before calling this property complete. |
| 6 | One realistic topic deliberately absent | **Retrieval/abstention verified; source interpretation partial** | The public Prospectus PDF “Transport and Hostel Facilities” is intentionally not in `sources.yaml`. The intended missing topic is detailed hostel/transport rules, not whether the university has such facilities. The golden set contains must-abstain cases, and the committed result reports 100% abstention correctness; a separate corpus coverage scan is not committed. |
| 7 | Exact-term content | **Verified** | Academic Rules Handbook 2026, PDF pages 10-11, has attendance clause **1.21**. PDF pages 24-25 have academic-warning clause **5.4**, including the **2.00 CGPA** threshold. The Prospectus rules also contain a grading table. |

## Limitations

- The scanned policy is present and identified, but without a Tesseract runtime
  the current extraction environment cannot OCR it. The same issue affects most
  pages of the 2023 rules PDF. Those pages must not be treated as searchable
  evidence until OCR is enabled and checked.
- The manifest verifies source bytes, not document interpretation. In
  particular, the assignment's version-sensitive examples and any changed
  clause wording in the 2023 edition remain unverified.
- The deliberate topic exclusion is a corpus design choice; the retrieval
  abstention test and coverage check are not implemented here.
