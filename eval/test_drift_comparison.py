from __future__ import annotations

import json
from pathlib import Path

from eval import run_drift_comparison


HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"


def test_drift_comparison_accepts_existing_complete_part_b_summary() -> None:
    summary_path = (
        RESULTS
        / "part-b-stochasticity-20261122T122424Z"
        / "summary.json"
    )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    run_drift_comparison.validate_part_b_summary(summary, summary_path)


def test_ingestion_facts_preserve_live_batch_b_evidence() -> None:
    evidence_path = (
        RESULTS
        / "part-a-chroma-20261122T120850Z-4dcc3be0"
        / "run.json"
    )
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))

    facts = run_drift_comparison.ingestion_facts(evidence)

    assert facts["verification_summary"] == {
        "total": 12,
        "index": 5,
        "quarantine": 7,
        "overall_decision": "quarantine_failures",
    }
    assert facts["unchanged_rerun"]["chunks_embedded"] == 0
    assert facts["live_user_collection_modified"] is False
