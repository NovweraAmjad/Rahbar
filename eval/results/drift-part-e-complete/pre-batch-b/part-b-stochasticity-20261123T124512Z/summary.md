# Assignment 03 Part B stochasticity summary

| Level | Metric | Run 1 | Run 2 | Absolute difference |
|---|---|---:|---:|---:|
| module | embedding_dimension | 768 | 768 | 0 |
| module | embedding_time_mean_seconds | 0.296258 | 0.276634 | 0.019624 |
| module | embedding_time_p95_seconds | 0.335252 | 0.301000 | 0.034252 |
| module | largest_determinism_difference | 0.000000 | 0.000000 | 0.000000 |
| module | mean_paraphrase_cosine | 0.831126 | 0.831126 | 0.000000 |
| module | mean_unrelated_cosine | 0.361897 | 0.361897 | 0.000000 |
| module | paraphrase_minus_unrelated | 0.469228 | 0.469228 | 0.000000 |
| module | paraphrase_pair_count | 15 | 15 | 0 |
| module | unrelated_pair_count | 15 | 15 | 0 |
| component | baseline_mrr | 0.565972 | 0.565972 | 0.000000 |
| component | baseline_ndcg_at_5 | 0.591356 | 0.591356 | 0.000000 |
| component | baseline_recall_at_3 | 0.600000 | 0.600000 | 0.000000 |
| component | baseline_recall_at_5 | 0.640000 | 0.640000 | 0.000000 |
| component | baseline_request_failures | 0 | 0 | 0 |
| component | typo_mrr | 0.538194 | 0.538194 | 0.000000 |
| component | typo_ndcg_at_5 | 0.570522 | 0.570522 | 0.000000 |
| component | typo_recall_at_3 | 0.600000 | 0.600000 | 0.000000 |
| component | typo_recall_at_5 | 0.640000 | 0.640000 | 0.000000 |
| component | typo_request_failures | 0 | 0 | 0 |
| end_to_end | abstention_correctness_percentage | 16.666667 | 16.666667 | 0.000000 |
| end_to_end | citation_validity_percentage | 100.000000 | 100.000000 | 0.000000 |
| end_to_end | faithfulness_percentage | 91.666667 | 91.666667 | 0.000000 |
| end_to_end | latency_p50_seconds | 0.077114 | 0.082215 | 0.005101 |
| end_to_end | latency_p95_seconds | 3.370777 | 2.383218 | 0.987560 |
| end_to_end | request_failures | 0 | 0 | 0 |
| end_to_end | successful_requests | 30 | 30 | 0 |

## Remaining variation

The module embedding path uses fixed dataset and provider settings. Component retrieval is deterministic when the live corpus and returned rankings are unchanged. End-to-end LLM text may vary because the provider uses temperature 0.2; provider restarts, cache behavior, model revisions, request failures, and live retrieval state can also affect results.
