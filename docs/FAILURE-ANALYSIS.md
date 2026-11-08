# Failure analysis

The preserved shipped baseline is `eval/baseline.json`: Recall@5 `83.3%`,
must-abstain correctness `100%`, and citation provenance validity `100%`.
Each experiment below changed one stage only and was restored before the next
experiment. The Stage B comparison outputs are under
`eval/chunk-tuning-runs/rt3-114805-10405/`; the other experiment evidence is
under `eval/failure-analysis-runs/20261107T144222Z/`.

## Stage B · Chunking

- **What we broke:** Used the existing `small` chunking profile
  (`chunk_size=200`, `chunk_overlap=25`) instead of the shipped
  `400`/`50` profile. This is the saved chunking perturbation; it is
  equivalent evidence for the chunking stage, although it is not the
  assignment's illustrative `100`-token/no-overlap setting.
- **Symptom:** Eight answerable questions failed. Recall@5 fell from
  `83.33%` to `75.00%`; gold-evidence relevance fell to `66.67%`.
  Citation provenance remained `100%` and must-abstain correctness remained
  `100%`, so the measurable regression is retrieval/chunk-boundary quality,
  not citation wiring or abstention.
- **Diagnosis, as if we had not caused it:** Compare the active chunk
  configuration and chunk count with the baseline, then inspect the evaluation
  gate. `small` produced 650 chunks and failed on
  `q01,q03,q05,q06,q07,q08,q10,q12`, while the restored profile produced 440
  chunks. The exact revealing number is `metrics.recall_at_5=0.75`, more than
  five percentage points below the committed `0.8333333333333334`.
- **Stage:** Chunking (B).
- **Remedy:** Restore `chunk_size=400` and `chunk_overlap=50`, re-index the
  affected collection, and rerun the evaluation.
- **Recovery evidence:** The comparison records the known-good `current`
  profile at Recall@5 `83.33%`, citation provenance `100%`, and
  must-abstain correctness `100%`. This is metric recovery, not a passing
  evaluation gate: the saved `current` profile still reports seven failed
  question checks.
- **How to diagnose without deliberately breaking it:** Treat any chunk
  configuration or corpus re-index as a versioned deployment; compare its
  configuration and chunk count with the known-good profile before rollout,
  then run the evaluation gate.
- **Exact metric/log:** `eval/chunk-tuning-runs/rt3-114805-10405/comparison.json`
  and `eval/chunk-tuning-runs/rt3-114805-10405/comparison.md` report
  `small.recall_at_5=0.75`, `current.recall_at_5=0.8333333333333334`, and the
  gate failure `Recall@5 dropped more than 5 percentage points below baseline`.

## Stage C · Retrieval

- **What we broke:** Set API `TOP_K=1` while leaving the corpus, embeddings,
  prompt, and generator unchanged.
- **Symptom:** Recall@5 fell from `83.33%` to `58.33%` and eight questions
  failed. Must-abstain correctness stayed `100%`.
- **Diagnosis, as if we had not caused it:** Check the API's effective
  `TOP_K` and compare the evaluation's `metrics.recall_at_5` with
  `eval/baseline.json`. The retrieval output also shows only one returned
  passage per request.
- **Stage:** Retrieval (C).
- **Remedy:** Restore API `TOP_K=5` and restart/reload only the API configuration.
- **Recovery evidence:** `retrieval-recovered.json` records the restored
  `TOP_K=5` run at Recall@5 `83.33%` with citation provenance `100%`.
  This is metric recovery; its evaluation gate still reports seven failed
  question checks.
- **How to diagnose without deliberately breaking it:** Monitor effective
  top-k and retrieved-count fields in request logs, and gate deployments on
  Recall@5. Check this before blaming the model.
- **Exact metric/log:** `retrieval-top-k-1.json` has
  `metrics.recall_at_5=0.5833333333333334`; `retrieval-recovered.json` has
  `metrics.recall_at_5=0.8333333333333334`.

## Stage D · Prompt

- **What we broke:** Removed the instruction to answer only from the supplied
  context (the weakened prompt was run against the prompt-specific questions).
- **Symptom:** All three must-abstain/prompt-injection cases were answered from
  context. Must-abstain correctness fell from `100%` to `0%`; all three
  questions failed.
- **Diagnosis, as if we had not caused it:** Inspect the effective prompt
  version/configuration and compare the request-level
  `answered_from_context` values with the expected `must_abstain` labels.
  Retrieval and citation metrics are unavailable for this all-abstention
  question set, so they must not be used as evidence for this failure.
- **Stage:** Prompt (D).
- **Remedy:** Restore the grounding/abstention instruction and reload the API.
- **Recovery evidence:** `prompt-recovered.json` records all three questions
  passing and `must_abstain_correctness_percentage=100.0`.
- **How to diagnose without deliberately breaking it:** Version the prompt
  template, log its version with each request, and run the must-abstain
  regression set in CI. Check A–E metrics before attributing the result to
  generation.
- **Exact metric/log:** `prompt-weakened-forced.json` reports
  `must_abstain_correctness_percentage=0.0` and three failed questions;
  `prompt-recovered.json` reports `100.0`.

## Stage E · Source/superseded filtering

- **What we broke:** Disabled the Chroma `superseded=False` filter while
  asking version-sensitive questions. The saved retrieval experiment and
  evaluation were performed against the existing Chroma data, which contains
  superseded 2023 rules chunks.
- **Symptom:** The disabled-filter version evaluation mixed documents from
  different years. For the 2023–24 question, it cited unrelated 2023–24
  pages and produced the wrong faculty total (`662` instead of the expected
  `735`); overall gold-evidence relevance was only `40.0%`, with three
  irrelevant citations for `q10`. Recall@5 remained `100%`, so retrieval
  recall alone did not expose the version error.
- **Diagnosis, as if we had not caused it:** Inspect each retrieved chunk's
  `source`, `doc_version`, and `superseded` metadata and compare citations with
  the question's expected version. The exact evaluation signal is
  `gold_evidence_relevance_percentage=40.0%` and the three `q10` entries in
  `gold_irrelevant_citations`.
- **Stage:** Source filtering (E).
- **Remedy:** Restore the `where={"superseded": False}` Chroma filter and
  re-run the version-sensitive query/evaluation. Do not delete or rewrite the
  existing Chroma data.
- **Recovery evidence:** `source-filter-current-policy-20261107.json` shows
  only current `2026-06` passages for the current-policy question, with
  `gold_evidence_relevance_percentage=100.0` and citation provenance
  `100.0%`. The direct retrieval output contains no `superseded=true`
  passage. This confirms filter-level recovery, but its evaluation gate still
  failed because the answerable question was incorrectly abstained on. The
  API implementation retains the filter in `api/app/retrieval/chroma.py`.
- **How to diagnose without deliberately breaking it:** Add a metadata
  invariant to retrieval telemetry/tests: no returned passage may have
  `superseded=true`, and a version-sensitive golden question must have
  citations from its expected `doc_version`. Review
  `gold_irrelevant_citations` and per-version relevance before blaming the
  model.
- **Exact metric/log:** `version-filter-disabled.json` reports
  `gold_evidence_relevance_percentage=40.0`, `recall_at_5=1.0`, and the q10
  irrelevant-citation records; `source-filter-current-policy-20261107.json`
  reports `gold_evidence_relevance_percentage=100.0` and
  `citation_provenance_validity_percentage=100.0`, while its gate reports one
  failed question.

## Stage F · Generation (incomplete)

- **What we broke:** Raised `TEMPERATURE` to `1.2`.
- **Observed symptom:** The single answerable version-sensitive question was
  retrieved correctly (`Recall@5=100%`) but the model returned an abstention
  (`answered_from_context=false`) instead of answering. The experiment
  therefore failed its question-level gate.
- **Diagnosis, as if we had not caused it:** Check the request's generation
  settings and correlate a failed answerable question with retrieval,
  `answered_from_context`, confidence, and citation count. Here retrieval and
  citation provenance were not the cause: the relevant 2026 chunk was in the
  top five, but the response abstained.
- **Stage:** Generation (F).
- **Remedy attempted:** Restore `TEMPERATURE=0.2`.
- **Recovery evidence:** `generation-recovered-temp-0.2.json` passed the
  answerable question with `answered_from_context=true`, Recall@5 `100%`,
  citation provenance `100%`, and zero failed questions.
- **Why this is not counted as a successful F experiment:** The saved
  `generation-temp-1.2.json` gate failed with one failed question and
  `must-abstain correctness is unavailable` because the one-question golden
  file contains no must-abstain cases. Its citation provenance and gold
  relevance were available at `100%`; the verified repeat
  `generation-temp-1.2-verified.json` instead returned no citation, making
  those metrics `null`. These are unavailable metrics where reported as
  `null`, not zeroes and not evidence of a passing generation experiment.
- **Exact metric/log:** `generation-temp-1.2.json` reports
  `answerable_incorrectly_abstained=1`, `recall_at_5=1.0`, and gate failures
  `1 question(s) failed evaluation checks` plus
  `must-abstain correctness is unavailable`.

## Not completed

Stages A (extraction) and the exact assignment variant of B
(fixed-size 100-token chunks with no overlap) were not run in this pass.
F was attempted and immediately restored, but remains inconclusive rather
than a successful experiment. The four valid, evidence-backed stage
experiments documented here are B (saved chunking perturbation), C, D, and E.
