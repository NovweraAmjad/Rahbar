# Assignment 03 Part B stochasticity summary

| Level | Metric | Run 1 | Run 2 | Absolute difference |
|---|---|---:|---:|---:|
| module | embedding_dimension | 768 | 768 | 0 |
| module | embedding_time_mean_seconds | 0.261149 | 0.243706 | -0.017443 |
| module | embedding_time_p95_seconds | 0.280419 | 0.276272 | -0.004147 |
| module | largest_determinism_difference | 0.000000 | 0.000000 | 0.000000 |
| module | mean_paraphrase_cosine | 0.831126 | 0.831126 | 0.000000 |
| module | mean_unrelated_cosine | 0.361897 | 0.361897 | 0.000000 |
| module | paraphrase_minus_unrelated | 0.469228 | 0.469228 | 0.000000 |
| component | baseline_mrr | 0.493056 | unavailable | unavailable |
| component | baseline_ndcg_at_5 | 0.516366 | unavailable | unavailable |
| component | baseline_recall_at_3 | 0.560000 | unavailable | unavailable |
| component | baseline_recall_at_5 | 0.560000 | unavailable | unavailable |
| component | typo_mrr | 0.454861 | 0.454861 | 0.000000 |
| component | typo_ndcg_at_5 | 0.487189 | 0.487189 | 0.000000 |
| component | typo_recall_at_3 | 0.520000 | 0.520000 | 0.000000 |
| component | typo_recall_at_5 | 0.560000 | 0.560000 | 0.000000 |
| end_to_end | abstention_correctness_percentage | 100.000000 | 100.000000 | 0.000000 |
| end_to_end | citation_validity_percentage | 100.000000 | 100.000000 | 0.000000 |
| end_to_end | faithfulness_percentage | 45.833333 | unavailable | unavailable |
| end_to_end | latency_p50_seconds | 1.597207 | 1.285030 | -0.312177 |
| end_to_end | latency_p95_seconds | 4.121848 | 3.788459 | -0.333388 |
| end_to_end | request_failures | 0 | 1 | 1 |
| end_to_end | successful_requests | 30 | 29 | -1 |

## Remaining variation

The module embedding path uses fixed dataset and provider settings. Component retrieval is deterministic when the live corpus and returned rankings are unchanged. End-to-end LLM text may vary because the provider uses temperature 0.2; provider restarts, cache behavior, model revisions, request failures, and live retrieval state can also affect results.
