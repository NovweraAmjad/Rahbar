# Batch B Manifest

This second, original test batch is stored under `corpus/batch-b/` and does not
modify any file in `corpus/raw/`.

| Document | Version | Intended planted issue | Relationship | Expected verification outcome | Detection |
| --- | --- | --- | --- | --- | --- |
| `batch-valid-attendance.txt` | 2026-11 | none | independent valid document | index | all checks pass |
| `batch-exact-a.txt` | 2026-11 | exact duplicate copy 1 | exact match with copy 2 | index | uniqueness exact |
| `batch-exact-b.txt` | 2026-11 | exact duplicate copy 2 | exact match with copy 1 | quarantine | uniqueness exact |
| `batch-near-a.txt` | 2026-11 | near duplicate copy 1 | near match with copy 2 | index | uniqueness near |
| `batch-near-b.txt` | 2026-11 | near duplicate copy 2 | near match with copy 1 | quarantine | uniqueness near |
| `batch-truncated.txt` | 2026-11 | explicit truncation marker | independent invalid document | quarantine | completeness |
| `batch-missing-title.txt` | 2026-11 | missing title metadata | independent invalid document | quarantine | completeness |
| `batch-corrupt.txt` | 2026-11 | invalid UTF-8 bytes | independent invalid document | quarantine | quality/extraction failure |
| `batch-academic-policy-2025.txt` | 2025 | superseded old version | older of Academic Policy pair | quarantine | recency/superseded |
| `batch-academic-policy-2026.txt` | 2026 | newer replacement | newer of Academic Policy pair | index | recency pass |
| `batch-injection.txt` | 2026-11 | prompt-injection instruction | independent unsafe document | quarantine | quality/prompt-injection |
| `batch-scholarship.txt` | 2026-11 | none | independent valid document | index | all checks pass |

The exact and near-duplicate outcomes are real results from the verifier's
content hashes and configurable cosine-similarity threshold. Batch B verification
reports and incremental change reports are written under `eval/results/`.
