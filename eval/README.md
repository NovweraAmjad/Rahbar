# Live golden evaluation

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
minikube mount "$(Resolve-Path .\corpus\raw):/mnt/rahbar-corpus"
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
