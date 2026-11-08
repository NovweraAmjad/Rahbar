# RAHBAR chunk tuning comparison

| Profile | Size | Overlap | Chunks | Recall@3 | Recall@5 | Citation provenance | Gold relevance | Must-abstain | Gate | Failed question IDs |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| small | 200 | 25 | 650 | 0.6667 | 0.7500 | 100.0% | 66.7% | 100.0% | fail | q01, q03, q05, q06, q07, q08, q10, q12 |
| current | 400 | 50 | 440 | 0.8333 | 0.8333 | 100.0% | 87.5% | 100.0% | fail | q03, q05, q06, q07, q08, q10, q12 |
| large | 800 | 100 | 355 | 0.7500 | 0.7500 | 100.0% | 85.7% | 100.0% | fail | q03, q05, q06, q07, q08, q12 |
