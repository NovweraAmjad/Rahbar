# Evaluation report

## Golden set and gate

`eval/golden.yaml` contains 15 questions: eight ordinary answerable questions,
two version-sensitive questions, two exact-term questions, two must-abstain
questions, and one prompt-injection question. The evaluator records retrieval
recall, citation provenance, gold-evidence relevance, abstention correctness,
request failures, and question-level failures.

The committed baseline and current result both report:

| Metric | Baseline/current |
| --- | ---: |
| Recall@3 | 83.33% |
| Recall@5 | 83.33% |
| Citation provenance validity | 100.00% |
| Must-abstain correctness | 100.00% |

The saved result is not a passing full evaluation gate: six answerable cases
incorrectly abstained and gold-evidence relevance was 46.15%. Citation
provenance and abstention gates passed. Generated answer correctness still
requires human review, as described in [eval/README.md](../eval/README.md).

## Failure experiments

The four evidence-backed experiments are documented in
[FAILURE-ANALYSIS.md](FAILURE-ANALYSIS.md), with Stage B comparison files under
`eval/chunk-tuning-runs/rt3-114805-10405/` and the other experiment files under
`eval/failure-analysis-runs/20261107T144222Z/`. Stage A and the exact assignment
100-token/no-overlap Stage B perturbation were not run; Stage F was attempted but
is explicitly marked inconclusive.

## CI fixture evaluation

The successful CD run used the fixture-specific baseline and completed its
evaluation gate before publishing/deploying. This is separate from the
committed full-corpus `eval/results.json` and does not replace it.
