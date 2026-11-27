# RAHBAR release gate

Decision: **FAIL**

| Check | Baseline | Candidate | Threshold | Result | Reason |
|---|---:|---:|---:|---|---|
| recall_at_5_regression | 64.000000 | 60.000000 | 2.000000 | FAIL | Recall@5 regression is 4.000000 percentage points (limit 2.000000). |
| faithfulness | 89.583333 | 85.416667 | 70.000000 | PASS | Faithfulness is 85.416667% (minimum 70.000000%). |
| p95_latency | 2.428376 | 3.569654 | 5.000000 | PASS | p95 latency is 3.569654s (maximum 5.000000s). |
| abstention_correctness | 16.666667 | 16.666667 | 16.666667 | PASS | Abstention correctness is 16.666667% versus baseline 16.666667%. |
