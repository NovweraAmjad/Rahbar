# Assignment 03 Part E data drift

- Generated: 2026-11-23T12:49:11.925483+00:00
- Baseline: `C:\Users\lenovo\Desktop\sem5\SCD\rahbar-starter\eval\results\drift-part-e-complete\pre-batch-b\part-b-stochasticity-20261123T124512Z\summary.json`
- Post-Batch-B: `C:\Users\lenovo\Desktop\sem5\SCD\rahbar-starter\eval\results\part-f-live-20261123\latest-complete\part-b-summary.json`
- Ingestion evidence: `C:\Users\lenovo\Desktop\sem5\SCD\rahbar-starter\eval\results\part-a-chroma-20261123T100513Z-2d6ca665\run.json`

## Batch B ingestion evidence

```json
{
  "status": "completed",
  "collection": "rahbar-pb-e792f0e2d58c46a0a044",
  "first_run": {
    "run_id": "b506b90883cd428aa1d926689868fc4d",
    "total_stored": 5,
    "chunks_embedded": 5,
    "chunks_skipped": 0,
    "chunks_removed": 0,
    "summary": {
      "discovered": 5,
      "unchanged": 0,
      "updated": 0,
      "new": 5,
      "deleted": 0,
      "total_chunks_embedded": 5,
      "total_chunks_skipped": 0,
      "total_chunks_removed": 0
    },
    "change_report_path": "C:\\Users\\lenovo\\Desktop\\sem5\\SCD\\rahbar-starter\\eval\\results\\part-a-chroma-20261123T100513Z-2d6ca665\\batch-b\\work\\state\\change-b506b90883cd428aa1d926689868fc4d.json"
  },
  "unchanged_rerun": {
    "chunks_embedded": 0,
    "chunks_skipped": 5
  },
  "verification_summary": {
    "total": 12,
    "index": 5,
    "quarantine": 7,
    "overall_decision": "quarantine_failures"
  },
  "quarantined_sources": [
    "batch-academic-policy-2025.txt",
    "batch-corrupt.txt",
    "batch-exact-b.txt",
    "batch-injection.txt",
    "batch-missing-title.txt",
    "batch-near-b.txt",
    "batch-truncated.txt"
  ],
  "chunks_by_source": {
    "batch-valid-attendance.txt": 1,
    "batch-exact-a.txt": 1,
    "batch-near-a.txt": 1,
    "batch-academic-policy-2026.txt": 1,
    "batch-scholarship.txt": 1
  },
  "live_user_collection_modified": false
}
```

## Metric changes

| Level | Metric | Baseline mean | Post-Batch-B mean | Change |
| --- | --- | ---: | ---: | ---: |
| component | baseline_recall_at_3 | 0.600000 | 0.720000 | 0.120000 |
| component | baseline_recall_at_5 | 0.640000 | 0.800000 | 0.160000 |
| component | baseline_mrr | 0.565972 | 0.676389 | 0.110417 |
| component | baseline_ndcg_at_5 | 0.591356 | 0.709035 | 0.117679 |
| end_to_end | faithfulness_percentage | 91.666667 | 95.833333 | 4.166667 |
| end_to_end | abstention_correctness_percentage | 16.666667 | 16.666667 | 0.000000 |
| end_to_end | citation_validity_percentage | 100.000000 | 100.000000 | 0.000000 |
| end_to_end | latency_p50_seconds | 0.079665 | 0.063560 | -0.016105 |
| end_to_end | latency_p95_seconds | 2.876997 | 2.290054 | -0.586944 |

## Evidence-based interpretation

Verification accepted 5 Batch B documents and quarantined 7; accepted sources were batch-academic-policy-2026.txt, batch-exact-a.txt, batch-near-a.txt, batch-scholarship.txt, batch-valid-attendance.txt. Consequently, retrieval and answer changes can be traced to the added active chunks, while quarantined duplicate, corrupt, truncated, superseded, missing-metadata, and injection documents did not enter the evaluated collection. Recall@5 changed by +0.160000; faithfulness changed by +4.166667 percentage points, abstention by +0.000000 percentage points, citation validity by +0.000000 percentage points, and p95 latency by -0.586944 seconds. The direction and magnitude are observations, not claims of improvement; corpus composition and live LLM behavior are the primary controlled inputs recorded in the source runs.

No change is attributed to model or pipeline improvements. The delivered values are the arithmetic means of two real Part B runs; individual run evidence remains in the referenced summary directories.
