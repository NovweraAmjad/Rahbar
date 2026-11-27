# Assignment 03 Part B stochasticity summary

| Level | Metric | Run 1 | Run 2 | Absolute difference |
|---|---|---:|---:|---:|
| module | embedding_dimension | 768 | 768 | 0 |
| module | embedding_time_mean_seconds | 0.372637 | 0.469832 | 0.097195 |
| module | embedding_time_p95_seconds | 0.544347 | 0.603389 | 0.059042 |
| module | largest_determinism_difference | 0.000000 | 0.000000 | 0.000000 |
| module | mean_paraphrase_cosine | 0.831126 | 0.831126 | 0.000000 |
| module | mean_unrelated_cosine | 0.361897 | 0.361897 | 0.000000 |
| module | paraphrase_minus_unrelated | 0.469228 | 0.469228 | 0.000000 |
| component | baseline_mrr | 0.625000 | 0.614583 | -0.010417 |
| component | baseline_ndcg_at_5 | 0.635911 | 0.627567 | -0.008344 |
| component | baseline_recall_at_3 | 0.640000 | 0.600000 | -0.040000 |
| component | baseline_recall_at_5 | 0.640000 | 0.640000 | 0.000000 |
| component | typo_mrr | 0.593750 | 0.593750 | 0.000000 |
| component | typo_ndcg_at_5 | 0.612189 | 0.612189 | 0.000000 |
| component | typo_recall_at_3 | 0.600000 | 0.600000 | 0.000000 |
| component | typo_recall_at_5 | 0.640000 | 0.640000 | 0.000000 |
| end_to_end | abstention_correctness_percentage | 16.666667 | 0.000000 | -16.666667 |
| end_to_end | citation_validity_percentage | 100.000000 | 100.000000 | 0.000000 |
| end_to_end | faithfulness_percentage | 75.000000 | 83.333333 | 8.333333 |
| end_to_end | latency_p50_seconds | 0.074189 | 0.077208 | 0.003019 |
| end_to_end | latency_p95_seconds | 4.124463 | 3.888978 | -0.235485 |
| end_to_end | request_failures | 0 | 0 | 0 |
| end_to_end | successful_requests | 30 | 30 | 0 |

## Remaining variation

The module embedding path uses fixed dataset and provider settings. Component retrieval is deterministic when the live corpus and returned rankings are unchanged. End-to-end LLM text may vary because the provider uses temperature 0.2; provider restarts, cache behavior, model revisions, request failures, and live retrieval state can also affect results.
