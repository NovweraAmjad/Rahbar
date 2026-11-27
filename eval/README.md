# Live golden evaluation

## Assignment 03 Part E: drift and coverage

The 40 labeled user-style queries are in [live_queries.yaml](live_queries.yaml):
28 exercise the existing golden domain and 12 represent corpus/test gaps.
`run_coverage_eval.py` uses the same production `app.embeddings.embed_query`
and `nomic-embed-text` provider as retrieval; it does not substitute a mock
embedding.

```powershell
python eval/run_coverage_eval.py `
  --name before-gap `
  --ollama-url http://127.0.0.1:11434
```

The similarity floor is fixed by default at cosine `0.80`. This is rounded down
from the measured 0.8311 mean for the 15 Part B paraphrase pairs and remains far
above the measured 0.3619 unrelated mean. The separate release-style coverage
minimum is the paper's 85% example. A valid run below that minimum writes its
complete JSON/Markdown evidence and exits 1; invalid input or provider failure
exits 2.

After adding golden questions for actual uncovered topics, compare against the
saved pre-gap result:

```powershell
python eval/run_coverage_eval.py `
  --name after-gap `
  --ollama-url http://127.0.0.1:11434 `
  --baseline-result eval/results/coverage-before-gap-20261123T094800Z/summary.json
```

Batch B must be ingested through the verification and incremental-indexing
workflow. The isolated live evidence runner copies the actual Batch B documents,
uses UUID-scoped Chroma collections, preserves every quarantine/archive action,
and proves a second run embeds zero chunks:

```powershell
kubectl -n rahbar port-forward service/chroma 18118:8000
python eval/run_batch_a2_evidence.py `
  --chroma-host 127.0.0.1 `
  --chroma-port 18118 `
  --ollama-url http://127.0.0.1:11434
```

It never deletes or modifies the live `rahbar` collection. Once the same verified
corpus is mounted in the isolated evaluation deployment, run the complete Part B
suite before and after the Batch B update and compare those timestamped
`summary.json` files:

```powershell
$env:PYTHONHASHSEED = "20261121"
$apiImage = kubectl get pods -n <namespace> -l app=chunk-api -o jsonpath='{.items[0].status.containerStatuses[0].imageID}'
python eval/run_part_b_suite.py `
  --api http://127.0.0.1:8080 `
  --api-image $apiImage `
  --host rahbar.local `
  --golden eval/golden.pre-part-e.jsonl `
  --ollama-url http://127.0.0.1:11434

python eval/run_drift_comparison.py `
  --baseline <pre-batch-b-part-b-summary.json> `
  --post-batch-b <post-batch-b-part-b-summary.json> `
  --ingestion-evidence <part-a-chroma-run.json>
```

The drift report compares the two-run means for retrieval, faithfulness,
abstention, citation validity, and p50/p95 latency. It fails rather than
interpreting missing metrics, and its prose is derived only from the supplied
Part B and ingestion evidence. `golden.pre-part-e.jsonl` is the byte-exact
30-question snapshot used by the pre-gap coverage run, so E3 can hold the
dataset constant while changing only the corpus.

## Assignment 03 Part C1: isolated chunk-size experiment

Part C1 changes only the indexing chunk size:

| Configuration | Chunk size | Chunk overlap |
|---|---:|---:|
| Baseline | 400 whitespace tokens | 50 whitespace tokens |
| Candidate | 800 whitespace tokens | 50 whitespace tokens |

The explicit configuration is [part-c1.yaml](part-c1.yaml). The runner
`run_part_c1.py` creates a fresh `rahbar-part-c1-<run-id>` namespace, an
isolated Chroma instance and Redis cache, and distinct collection/cache scopes
for the baseline and candidate. It does not apply to or modify the live
`rahbar` namespace or its deployment.

The runner verifies the corpus fingerprint, builds and applies only generated
copies of the chunk-tuning manifests, waits for each indexer Job, validates the
run-specific collection, and scales the experiment API between configurations.
For each configuration it runs the complete Part B stochasticity suite twice:
module embedding, component retrieval with typo noise, and real end-to-end
answer evaluation. Model tags, corpus bytes, golden set, embedding provider,
generation provider/model, top-k, relevance floor, cache threshold,
temperature, and overlap remain constant.

Run it with a new, lowercase, hyphenated output directory name:

```powershell
kubectl config use-context kind-civicpulse
python eval/run_part_c1.py --output-dir eval/results/part-c1-20261122T145801Z
```

Successful runs write the following under `eval/results/<run-directory>/`:

- `configuration.yaml`: exact explicit baseline/candidate configuration
- `baseline/` and `candidate/`: collection metadata and complete Part B run 1/2 artifacts
- `individual-results.json`: complete individual result summaries
- `comparison.json`: machine-readable baseline/candidate comparison
- `comparison.md`: readable comparison across module, component, and end-to-end levels
- `run.json`: corpus, image, provider, and collection provenance

The runner refuses to overwrite an existing output directory or reuse an
existing run-specific collection. It does not release or replace the live
deployment.

## Assignment 03 Part C2 release gate

`release-gate.yaml` contains separate release thresholds. The current thresholds
are justified from the measured C1 baseline mean (two Part B stochasticity runs):

- Recall@5 mean: 64.0%. The gate permits at most a 2.0 percentage-point
  regression.
- Faithfulness mean: 79.1667%. The minimum is 70.0%, preserving the
  answer-supported requirement while allowing 9.1667 percentage points for
  experiment variance.
- p95 latency mean: 4.0067 seconds. The maximum is 5.0 seconds, or 24.8%
  headroom above the measured baseline mean.
- Abstention correctness must not be lower than the measured baseline mean
  (8.3333%).

The gate reads a baseline and candidate Part B stochasticity `summary.json`,
requires both run levels and all configured metrics, compares the two-run means,
prints PASS or FAIL reasons, writes JSON and Markdown reports, and exits 0 only
on PASS.

```powershell
python eval/run_release_gate.py `
  --baseline eval/results/c1-.../baseline/part-b/part-b-stochasticity-.../summary.json `
  --candidate eval/results/c1-.../candidate/part-b/part-b-stochasticity-.../summary.json `
  --config eval/release-gate.yaml `
  --out eval/results/release-gates `
  --name passing-candidate
```

Use `--name failing-candidate` with a separate deliberately bad actual candidate
result to demonstrate FAIL. Invalid configuration or missing metrics exit 2,
while a valid but failed gate exits 1. Gate reports do not change a deployment.

## Complete Part B stochasticity runner

Run the complete module, component, and end-to-end suite twice with one command:

```powershell
$env:PYTHONHASHSEED = "20261121"
$namespace = "your-namespace"
$apiImage = kubectl get pods -n $namespace -l app=chunk-api -o jsonpath='{.items[0].status.containerStatuses[0].imageID}'
python eval/run_part_b_suite.py --api http://localhost:8080 --api-image $apiImage --host rahbar.local --golden eval/golden.jsonl --ollama-url http://172.23.112.1:11434
```

Set `PYTHONHASHSEED` before starting Python; changing it inside the runner cannot
affect the already-initialized interpreter. The runner requires the API image
reference reported by the running pod, including its `@sha256:` digest; mutable
tags such as `:dev` are rejected.

The runner creates a new timestamped directory under
`eval/results/part-b-stochasticity-YYYYMMDDTHHMMSSZ/`, preserving every previous
run. It records fixed seeds, dataset and runner metadata, model/provider
identity, each complete run, absolute metric differences, and a readable
Markdown summary table.

## End-to-end answer evaluation

`run_end_to_end_eval.py` calls the real RAHBAR `/api/ask` endpoint for every
golden question and records the actual provider answer, retrieved evidence,
citations, request status, and latency.

```powershell
python eval/run_end_to_end_eval.py --api http://localhost:8080 --host rahbar.local --golden eval/golden.jsonl
```

The runner writes timestamped `summary.json` and `per-question.json` files under
`eval/results/end-to-end-.../`. It reports faithfulness, abstention correctness,
citation validity, and latency separately from retrieval metrics. Faithfulness
uses a conservative citation/context check: an answer is counted as faithful
only when `answered_from_context` is true and every emitted citation matches
retrieved source/page/section evidence. Unsupported or absent evidence is not
classified as faithful.

## Component-level retrieval evaluation

`run_component_retrieval_eval.py` runs the existing RAHBAR API evaluator twice:

1. **Baseline**: unchanged retrieval configuration and original golden questions.
2. **Controlled typo noise**: one deterministic adjacent-character swap in each
   evaluated question, without changing retrieval configuration or the corpus.

It records per-question ranked retrieval details and writes a timestamped
comparison under `eval/results/component-retrieval-YYYYMMDDTHHMMSSZ/`.

```powershell
python eval/run_component_retrieval_eval.py --api http://localhost:8080 --host rahbar.local --golden eval/golden.jsonl
```

The component metrics are calculated only across answerable golden questions:

- `recall_at_3`: fraction of gold document/page evidence found in the top 3.
- `recall_at_5`: fraction of gold document/page evidence found in the top 5.
- `mrr`: mean reciprocal rank of the first gold evidence match.
- `ndcg_at_5`: mean binary gain nDCG@5, with unique gold evidence items as gains
  and duplicated ranked passages counted only once per evidence item.

The noise summary reports baseline, noisy absolute values, and
`absolute_change = noisy - baseline` for every metric.

## Module-level embedding evaluation

`module-embedding-pairs.yaml` defines 15 labeled paraphrase pairs and 15 labeled
unrelated pairs. `run_module_embedding_eval.py` uses RAHBAR's production
`app.embeddings.embed_query` implementation and the configured `OLLAMA_URL` /
`EMBED_MODEL` values. It refuses to report results if the Ollama model cannot
be resolved or a request fails.

Run it from the repository root:

```powershell
python eval/run_module_embedding_eval.py
```

To use a specific Ollama endpoint or model:

```powershell
python eval/run_module_embedding_eval.py --ollama-url http://172.23.112.1:11434 --model nomic-embed-text
```

The runner writes `summary.json`, `pair-results.json`, and `timing-results.json`
in a timestamped directory under `eval/results/module-embedding-.../`.
It records the fixed seed, pair dataset checksum, runner checksum, resolved
model name and digest, embedding dimension, timing method, pair-level cosine
scores, mean paraphrase/unrelated similarity, the difference, and deterministic
repeat differences.

## Assignment 03 golden dataset

`golden.jsonl` contains 36 reviewed questions: 24 answerable, 12 abstention
cases, and 5 cases marked `hard`. The original 30 remain unchanged with their
provenance; Part E adds six human-reviewed abstention cases for the measured
live-query gaps. The full dataset also contains paraphrased, multi-chunk,
version-sensitive, and Batch B cases.

Each JSONL record has:

- `id`, `category`, and `question`
- `origin`: `human` or `synthetic`
- `manual_review`: status and reviewer
- `expected_answerability`
- `expected_answer` for answerable questions
- `gold_evidence`: source filename, 1-based PDF/text page, section, and version

Answerable records must have a non-empty answer and at least one evidence item.
Abstention records must not contain evidence or an answer. Version-sensitive
records must name both the expected document and version. Synthetic records
require manual review; the dataset currently records at least 8 reviewed
synthetic questions.

Validate the dataset and print actual counts:

```powershell
python eval/golden_schema.py eval/golden.jsonl
python -m pytest eval/test_golden_dataset.py eval/test_run_eval.py -v
```

The validation checks labels and structure only. The `test_golden_dataset.py`
page checks verify selected answers against text extracted from the actual PDF
or Batch B source pages. Human and synthetic labels describe writing provenance;
they are not inferred from wording.

`golden.yaml` contains 15 grounded questions: 8 ordinary answerable questions,
2 version-sensitive questions, 2 exact-term questions, 2 must-abstain questions,
and 1 prompt-injection question. The answers are editorial references for review;
the runner does not compare generated wording because answer correctness still
requires human review.

## Gold evidence and metrics

Each answerable case names the indexed source filename and PDF page, plus the
section when that metadata can be stated reliably. Retrieval matches require an
exact source filename; if gold evidence specifies a page, the page must also
match; if it specifies a section, section text must match after whitespace and
case normalization. A document-level result does not match gold evidence that
specifies a page. Recall@k is the fraction of gold evidence items present in the
first *k* retrieved references, aggregated by evidence item overall and by
category.

Citation metrics distinguish provenance from relevance:

- **Citation provenance validity** is the percentage of emitted citations whose
  source and page match a reference retrieved for that request. Sections must
  also match when both citation and retrieved-reference section metadata are
  available. This is the assignment's citation-validity metric and its 95%
  gate.
- **Gold-evidence relevance** is the percentage of emitted citations that are
  provenance-valid and also match a gold evidence item under the strict source,
  page, and supplied-section rule above. It is an additional diagnostic; it
  does not replace or relax provenance validation.

Both percentages are also recorded per question, with each citation's
provenance and gold-relevance result and its matching retrieved/gold metadata.
Percentages are unavailable when there are no emitted citations. A must-abstain
case is correct only when `answered_from_context` is false and no citations are
returned. Request failures are reported, never counted as successes; metrics
that need a complete set are marked unavailable rather than treated as zero.

The pass/fail detail checks answerability, retrieval of all gold evidence by
rank 5, and 100% gold-evidence relevance for answerable cases; it checks the abstention
rule for must-abstain and prompt-injection cases. Generated answer correctness
is not an automatic metric. CI gates on no request errors, 100% must-abstain
correctness, at least 95% citation provenance validity, and no Recall@5 drop
greater than 5 percentage points against an available baseline.

## Run against Minikube Ingress

Start or confirm the local ingress controller and forward its HTTP service:

```powershell
kubectl -n ingress-nginx port-forward service/ingress-nginx-controller 8080:80
```

In another terminal, run from the repository root:

```powershell
python eval/run_eval.py
```

The runner defaults to `http://localhost:8080` and sends `Host: rahbar.local`,
as required by the Ingress rule. The endpoint and host can be overridden:

```sh
RAHBAR_EVAL_BASE_URL=http://localhost:8080 python eval/run_eval.py
```

On PowerShell, set the environment variable before running Python:

```powershell
$env:RAHBAR_EVAL_BASE_URL = "http://localhost:8080"
python eval/run_eval.py
```

`RAHBAR_EVAL_HOST` or `--host` changes the Host header. Use `--api` to override
the URL directly, `--golden path/to/golden.yaml` to select another dataset, and
`--out path/to/results.json` to select the output file. For example:

```powershell
python eval/run_eval.py --api http://localhost:8080 --host rahbar.local --golden eval/golden.yaml --out eval/results.json
```

The full response and request ID for each case, failures, metrics, and gate
result are saved to `eval/results.json`. The runner creates
`eval/baseline.json` after a run with no request failures, only if that baseline
does not already exist. The snapshot records whether the evaluation gate
passed; a non-passing snapshot is still a record of measured results, not
evidence that the API passed. Later runs compare Recall@5 against the existing
baseline and never overwrite it.

Review a later result before refreshing the baseline. Preserve the old snapshot,
then create a candidate at a separate path:

```powershell
Copy-Item eval/baseline.json eval/baseline-approved-previous.json
python eval/run_eval.py --baseline eval/baseline-candidate.json
```

After reviewing the candidate metrics and question-level details, replace the
approved baseline explicitly only if the gate passes and the run is accepted;
retain the archived copy. Results and baselines contain no API credentials; do
not put credentials in the URL or golden file.

## Semantic-cache threshold evaluation

`cache-threshold-pairs.yaml` defines explicit `expected_reuse` labels and
rationales for equivalent questions, paraphrases, hard negatives, version- and
entity-sensitive queries, and unrelated queries. Labels are set from whether
the cached answer is appropriate for the new question; they are not derived
from the measured similarity. The set includes difficult negatives involving
attendance exceptions, academic warning rules, course repetition, report years,
and distinct policy questions.

The runner embeds each unique question once through the API's production
`embed_query` helper at Ollama `/api/embed` and uses the production cache's
cosine calculation and inclusive `similarity >= threshold` decision. The model
and endpoint come from `EMBED_MODEL` and `OLLAMA_URL`, defaulting to
`nomic-embed-text` and the current Minikube-configured endpoint. The runner
checks and records the resolved Ollama model digest before and after embedding.
No Kubernetes deployment or Redis server is needed.

For each threshold, the output reports true hits (expected reuse and hit),
false hits (unexpected reuse), missed hits (expected reuse but miss), and true
misses. Hit rate is all actual cache decisions divided by all pairs; precision
is true hits divided by actual hits; positive-pair recall is true hits divided
by all expected-reuse pairs; false-hit rate is false hits divided by expected
non-reuse pairs. A metric whose denominator is zero is reported as unavailable
(`null`). The lowest tested threshold with zero false hits is reported when one
exists. A zero observed false-hit rate is not proof of production safety.

Run from the repository root with the Ollama endpoint reachable:

```powershell
$env:OLLAMA_URL = "http://172.23.112.1:11434"
$env:EMBED_MODEL = "nomic-embed-text"
python eval\run_cache_threshold_eval.py --out-dir eval\cache-threshold-runs\trial-20261107
```

Choose a new output directory for each run; the runner refuses to overwrite an
existing directory. It saves `pair-results.json` with every similarity and
decision, and `summary.json` with aggregate metrics, dataset checksum, model
digest, and the evaluation assumptions. Pair scenarios are isolated: each is
treated as one valid grounded cache entry, with a fixed collection and
generation and no cross-pair entries. The harness does not execute Redis
operations or test re-index invalidation or Redis failure handling; those
behaviors remain covered by production cache/indexer tests.

Completed run `20261107T171600` used 20 labeled pairs (8 expected reuses and 12
expected non-reuses), `nomic-embed-text:latest`, digest
`0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f`:

| Threshold | True hits | False hits | Missed hits | Hit rate | Precision | Positive-pair recall | False-hit rate |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.90 | 5 | 1 | 3 | 30.00% | 83.33% | 62.50% | 8.33% |
| 0.93 | 3 | 0 | 5 | 15.00% | 100.00% | 37.50% | 0.00% |
| 0.95 (current) | 2 | 0 | 6 | 10.00% | 100.00% | 25.00% | 0.00% |
| 0.97 | 1 | 0 | 7 | 5.00% | 100.00% | 12.50% | 0.00% |

The 0.90 false hit was `report-year-2023-vs-2019`, similarity 0.9112: the
2019–20 faculty-count question would reuse a response cached for the 2023–24
faculty total. The lowest tested threshold with zero false hits was 0.93. This
small test set does not establish that 0.93 or any threshold is generally safe;
the production threshold remains 0.95 pending broader, answer-reviewed data.

## Reproducible chunk-tuning experiment

`chunk-tuning.yaml` fixes the three sequential configurations: small
(200/25), current (400/50), and large (800/100), where values are whitespace
tokens. The runner keeps the 15-question golden set, corpus bytes, embedding
and generation model tags, `TOP_K`, relevance floor, cache threshold, and
temperature fixed. It reuses `run_eval.py`; Recall@3/@5 and both citation
metrics retain the definitions above.

Each run gets a unique output directory under `eval/chunk-tuning-runs/` and
three distinct Chroma collections incorporating the run ID and profile. The
runner refuses an existing output directory, an existing experiment namespace,
or an existing profile collection. The experiment stack uses only the
`rahbar-chunk-tuning` namespace, its own Chroma and Redis PVCs, and a one-replica
API. It never uses the `rahbar` namespace, its PVCs, its collections,
`eval/results.json`, or `eval/baseline.json`.

Before each index, the runner runs `corpus/fetch.py --verify`, validates the
index-listed files against their SHA-256 values, and checks that the corpus
fingerprint has not changed. The profile's stored collection count and embedding
metadata are checked before the API is switched to that collection. The API
cache already namespaces both entries and generation keys by collection; the
runner additionally uses a separate Redis service and records the resulting
cache scope for each profile. Indexing and evaluations run one profile at a
time to avoid simultaneous requests to Ollama; the runner waits for the API pod
to terminate before starting the next profile's indexing Job.

### Prepare and run on Minikube

Run these commands from the repository root in PowerShell. First ensure the
Minikube context is selected, Ollama is reachable at
`http://172.23.112.1:11434`, and both configured model tags
(`nomic-embed-text` and `llama3.2:3b`) are available.

```powershell
minikube status
kubectl config use-context minikube
```

Verify the local corpus and build/load the API and indexer images:

```powershell
python corpus\fetch.py --verify
docker build -t rahbar-api:dev api
docker build -t rahbar-indexer:dev indexer
minikube image load rahbar-api:dev
minikube image load rahbar-indexer:dev
```

In a separate PowerShell terminal, keep the corpus mount running for the whole
experiment:

```powershell
minikube mount "$(Resolve-Path .\corpus):/mnt/rahbar-corpus"
```

Then run the experiment:

```powershell
python eval\run_chunk_tuning.py
```

The runner creates a unique output directory automatically. To choose a
specific new directory, provide a unique lowercase run ID as its final path
component:

```powershell
python eval\run_chunk_tuning.py --output-dir eval\chunk-tuning-runs\trial-20261107
```

The runner applies only `k8s/experiments/chunk-tuning/`, checks that
`rahbar-chunk-tuning` does not already exist, indexes and evaluates each
profile sequentially, and stops on any indexing, API request, corpus,
collection, or incomplete-evaluation failure. A complete evaluation can still
contain failed questions; those are reported as measured results, not hidden
or treated as infrastructure failures. Evaluation-gate status and gate failure
reasons are also retained per profile. An incomplete run exits nonzero and has
no comparison summary; a complete run with one or more failed metric gates
preserves the full comparison and exits nonzero to make that outcome explicit.

Each run directory contains the Git commit, image references and runtime image
IDs, verified corpus fingerprint and file hashes, model tags, and common
settings. Each profile subdirectory contains its configuration, generated
ConfigMap and Job YAML, indexing log, collection count/metadata, raw
`run_eval.py` JSON, evaluation log, and failed question IDs. A comparison is
written only after all three profiles complete successfully:

```text
eval/chunk-tuning-runs/<run-id>/comparison.json
eval/chunk-tuning-runs/<run-id>/comparison.md
```

`comparison.md` reports chunk count, Recall@3/@5, citation provenance validity,
gold-evidence relevance, must-abstain correctness, and failed IDs. After
reviewing all three complete runs, copy the **measured** Recall@5 values and
your evidence-based shipped-configuration rationale into the table in
`docs/PIPELINE.md`. Do not fill that table from an incomplete run or from
assumed values. The runner reads the existing baseline and checks its SHA-256;
it never writes the baseline or the ordinary evaluation results file.

### Completed run: `rt3-114805-10405`

The completed comparison is preserved at
`eval/chunk-tuning-runs/rt3-114805-10405/` (`comparison.json` and
`comparison.md`). All three profiles completed their evaluations, but all
three failed the overall evaluation gate:

| Profile | Size / overlap | Chunks | Recall@3 | Recall@5 | Citation provenance | Gold-evidence relevance | Must-abstain | Failed questions |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Small | 200 / 25 | 650 | 66.67% | 75.00% | 100.00% | 66.67% | 100.00% | 8 / 15 |
| Current | 400 / 50 | 440 | 83.33% | 83.33% | 100.00% | 87.50% | 100.00% | 7 / 15 |
| Large | 800 / 100 | 355 | 75.00% | 75.00% | 100.00% | 85.71% | 100.00% | 6 / 15 |

The 400/50 current profile is selected based on its highest measured Recall@5
and gold-evidence relevance. Selection does not mean the evaluation gate
passed. Citation provenance validity checks whether citations refer to
retrieved material; gold-evidence relevance separately checks whether those
citations match the expected evidence for each question. Therefore, 100%
provenance validity does not imply 100% gold-evidence relevance.

### Recovering from a failed run

Collection existence, count, and metadata are read through Chroma's HTTP API
using a temporary port-forward to `service/chunk-chroma`; the runner does not
require Python in the Chroma container. It stops on inspection errors and
preserves the run directory and any namespace/PVC state for inspection.

The reported failed run `20261107t1106-56528f` stopped before submitting its
indexer Job. Its `small/collection.json` records `not_indexed`. The experiment
namespace was absent when this recovery note was written; check again before
retrying because cluster state may have changed.

From the repository root, first confirm the selected cluster and inspect only
the disposable namespace:

```powershell
kubectl config current-context
kubectl get namespace rahbar-chunk-tuning
kubectl get pods,services,pvc,jobs -n rahbar-chunk-tuning
```

If that namespace exists, preserve/review the run artifacts and confirm it is
the disposable experiment namespace before cleanup. Deleting it also deletes
its PVCs (and may leave PVs depending on the storage reclaim policy):

```powershell
kubectl delete namespace rahbar-chunk-tuning
kubectl wait --for=delete namespace/rahbar-chunk-tuning --timeout=120s
```

Skip namespace cleanup if it is already absent. Never delete or modify
`rahbar`, its PVCs, or its collections. Keep the failed output directory as
evidence; the runner intentionally refuses to reuse it. Once the intended
Minikube context, corpus mount, images, and Ollama endpoint are ready, rerun
with a new output directory:

```powershell
$runId = (Get-Date -Format "yyyyMMddTHHmmss").ToLower()
python eval\run_chunk_tuning.py --output-dir "eval\chunk-tuning-runs\retry-$runId"
```

### Cleanup

Review and retain the output directory before cleanup. The disposable namespace
contains the experiment's only Chroma and Redis PVCs; deleting it removes those
PVCs and may delete their backing storage according to the cluster's storage
class reclaim policy:

```powershell
kubectl delete namespace rahbar-chunk-tuning
```

This command is intentionally scoped to the experiment namespace. Never replace
that name with `rahbar`, and do not delete any PV or PVC by a broad selector.
The host `minikube mount` can be stopped with Ctrl+C after the run and cleanup.
