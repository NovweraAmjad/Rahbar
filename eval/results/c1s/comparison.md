# Assignment 03 Part C1 comparison

Only `chunk_size` changed: baseline 400 whitespace tokens, candidate 800 whitespace tokens. `chunk_overlap` remained 50 tokens.

| Level | Metric | Baseline mean | Candidate mean | Absolute change | Relative change |
| --- | --- | ---: | ---: | ---: | ---: |
| module | embedding_dimension | 768 | 768 | 0 | 0.000000 |
| module | embedding_time_mean_seconds | 0.249399 | 0.252510 | 0.003110 | 1.247167 |
| module | embedding_time_p95_seconds | 0.271482 | 0.280697 | 0.009214 | 3.394032 |
| module | largest_determinism_difference | 0.000000 | 0.000000 | 0.000000 | unavailable |
| module | mean_paraphrase_cosine | 0.831126 | 0.831126 | 0.000000 | 0.000000 |
| module | mean_unrelated_cosine | 0.361897 | 0.361897 | 0.000000 | 0.000000 |
| module | paraphrase_minus_unrelated | 0.469228 | 0.469228 | 0.000000 | 0.000000 |
| component | baseline_mrr | 0.619792 | 0.552083 | -0.067708 | -10.924370 |
| component | baseline_ndcg_at_5 | 0.631739 | 0.570522 | -0.061217 | -9.690164 |
| component | baseline_recall_at_3 | 0.620000 | 0.560000 | -0.060000 | -9.677419 |
| component | baseline_recall_at_5 | 0.640000 | 0.600000 | -0.040000 | -6.250000 |
| component | typo_mrr | 0.593750 | 0.520833 | -0.072917 | -12.280702 |
| component | typo_ndcg_at_5 | 0.612189 | 0.546801 | -0.065388 | -10.681093 |
| component | typo_recall_at_3 | 0.600000 | 0.520000 | -0.080000 | -13.333333 |
| component | typo_recall_at_5 | 0.640000 | 0.600000 | -0.040000 | -6.250000 |
| end_to_end | abstention_correctness_percentage | 16.666667 | 16.666667 | 0.000000 | 0.000000 |
| end_to_end | citation_validity_percentage | 100.000000 | 100.000000 | 0.000000 | 0.000000 |
| end_to_end | faithfulness_percentage | 89.583333 | 85.416667 | -4.166667 | -4.651163 |
| end_to_end | latency_p50_seconds | 0.074581 | 0.069591 | -0.004990 | -6.690670 |
| end_to_end | latency_p95_seconds | 2.428376 | 3.569654 | 1.141278 | 46.997587 |
| end_to_end | request_failures | 0 | 0 | 0 | unavailable |
| end_to_end | successful_requests | 30 | 30 | 0 | 0.000000 |

## Isolated configuration details

- Baseline collection: `rahbar-ct-c1s-baseline` (202 chunks).
- Candidate collection: `rahbar-ct-c1s-candidate` (169 chunks).
- The live RAHBAR deployment was not modified or rolled out.

## Constants

- `ARCHIVE_DIR=/tmp/archive`
- `CACHE_PREFIX=rahbar:semantic-cache`
- `CACHE_SIMILARITY_THRESHOLD=0.95`
- `CHROMA_HOST=chunk-chroma`
- `CHROMA_PORT=8000`
- `CORPUS_DIR=/corpus`
- `EMBED_MODEL=nomic-embed-text`
- `LLM_MODEL=llama3.2:3b`
- `LLM_PROVIDER=ollama-host`
- `LOG_LEVEL=INFO`
- `OLLAMA_URL=http://172.23.112.1:11434`
- `QUARANTINE_DIR=/tmp/quarantine`
- `REDIS_URL=redis://chunk-redis:6379/0`
- `RELEVANCE_FLOOR=0.5`
- `RETRIEVER=chroma`
- `TEMPERATURE=0.2`
- `TOP_K=5`
- `VERIFICATION_REPORT_DIR=/tmp/verification-reports`

The baseline and candidate means are the arithmetic means of the two Part B stochasticity runs. Individual run files remain under each configuration directory.
