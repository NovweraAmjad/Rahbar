# Assignment 03 Part B stochasticity summary

| Level | Metric | Run 1 | Run 2 | Absolute difference |
|---|---|---:|---:|---:|
| module | embedding_dimension | 768 | 768 | 0 |
| module | embedding_time_mean_seconds | 0.261454 | 0.243566 | -0.017888 |
| module | embedding_time_p95_seconds | 0.291779 | 0.269614 | -0.022164 |
| module | largest_determinism_difference | 0.000000 | 0.000000 | 0.000000 |
| module | mean_paraphrase_cosine | 0.831126 | 0.831126 | 0.000000 |
| module | mean_unrelated_cosine | 0.361897 | 0.361897 | 0.000000 |
| module | paraphrase_minus_unrelated | 0.469228 | 0.469228 | 0.000000 |
| component | baseline_mrr | 0.552083 | 0.552083 | 0.000000 |
| component | baseline_ndcg_at_5 | 0.570522 | 0.570522 | 0.000000 |
| component | baseline_recall_at_3 | 0.560000 | 0.560000 | 0.000000 |
| component | baseline_recall_at_5 | 0.600000 | 0.600000 | 0.000000 |
| component | typo_mrr | 0.520833 | 0.520833 | 0.000000 |
| component | typo_ndcg_at_5 | 0.546801 | 0.546801 | 0.000000 |
| component | typo_recall_at_3 | 0.520000 | 0.520000 | 0.000000 |
| component | typo_recall_at_5 | 0.600000 | 0.600000 | 0.000000 |
| end_to_end | abstention_correctness_percentage | 33.333333 | 0.000000 | -33.333333 |
| end_to_end | citation_validity_percentage | 100.000000 | 100.000000 | 0.000000 |
| end_to_end | faithfulness_percentage | 79.166667 | 91.666667 | 12.500000 |
| end_to_end | latency_p50_seconds | 0.070817 | 0.068366 | -0.002451 |
| end_to_end | latency_p95_seconds | 3.566894 | 3.572414 | 0.005519 |
| end_to_end | request_failures | 0 | 0 | 0 |
| end_to_end | successful_requests | 30 | 30 | 0 |

## Remaining variation

The module embedding path uses fixed dataset and provider settings. Component retrieval is deterministic when the live corpus and returned rankings are unchanged. End-to-end LLM text may vary because the provider uses temperature 0.2; provider restarts, cache behavior, model revisions, request failures, and live retrieval state can also affect results.
