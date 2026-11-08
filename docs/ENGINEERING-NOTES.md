# Engineering notes

These answers use the current source and committed measurements. Line references
are intentionally included so each claim can be checked against the repository.

## 1. Indexing in API startup

With three API replicas, moving indexing into API startup would make all replicas
attempt the same write against one Chroma volume. They would race on collection
creation/upserts, delay or fail readiness, and repeat expensive extraction and
embedding during every rollout. The design prevents this with a separate
`batch/v1 Job` and a CronJob: [k8s/base/indexer-job.yaml](../k8s/base/indexer-job.yaml#L1-L12)
and [k8s/base/indexer-cronjob.yaml](../k8s/base/indexer-cronjob.yaml#L1-L22).
The API Deployment contains only the API container and read-side dependencies:
[k8s/base/api.yaml](../k8s/base/api.yaml#L20-L53).

## 2. Readiness and liveness swapped

`/healthz` is dependency-free, while `/readyz` checks dependencies and the
indexed collection. The current probes are explicit in
[k8s/base/api.yaml](../k8s/base/api.yaml#L69-L92). If swapped while Chroma took
90 seconds to start, liveness would fail repeatedly and restart all three API
replicas instead of allowing them to become ready; the deployment could enter a
restart loop. Keeping liveness on `/healthz` lets startup/readiness absorb slow
dependencies without turning a temporary dependency outage into process
restarts.

## 3. CPU HPA on generation

The HPA measures average container CPU utilization against requested CPU, not
queue depth, request latency, tokens generated, or Ollama saturation. The
current signal and limits are in [k8s/base/hpa.yaml](../k8s/base/hpa.yaml#L1-L20):
70% CPU, three to eight API replicas. For a generation service, queue depth,
in-flight requests, time-to-first-token, and provider latency are more useful
signals. Using them would require instrumentation, a metrics adapter, and an
autoscaling metric exposed to Kubernetes; this repository does not implement
that custom-metrics path.

## 4. Indexer memory, margin, and exit behavior

The indexer requests 1 GiB and has a 3 GiB memory limit:
[k8s/base/indexer-job.yaml](../k8s/base/indexer-job.yaml#L25-L34). A real
`kubectl top pod` peak measurement is not committed, so the runtime peak and
therefore the numeric margin cannot honestly be reported here. If the process
exceeds the container limit, Kubernetes terminates it with an OOM kill; the
usual container exit code is 137, and the Job's `OnFailure` policy retries it
until `backoffLimit` is reached. Collect `kubectl top pods -n rahbar` during a
real indexing run before claiming a measured margin.

## 5. Recall@5 by question type

The committed baseline reports overall Recall@5 of 83.33%. The per-category
baseline shows ordinary answerable questions at 50%, version-sensitive at
87.5%, exact-term at 0% in the saved run, and must-abstain cases as not
applicable to recall. Exact terms are hardest because embeddings smooth rare
clause numbers, codes, and numeric thresholds into semantically similar text.
The relevant lecture technique is hybrid lexical/semantic retrieval or an
exact-term reranking path; the current implementation records this limitation
but does not claim that such a path is implemented. See
[eval/baseline.json](../eval/baseline.json#L1-L48).

## 6. Changing `EMBED_MODEL` on the API only

The API checks the requested embedding model and vector dimension against
Chroma collection metadata before serving retrieval. A model mismatch therefore
fails readiness/validation instead of silently querying incompatible vectors.
The guardrail is implemented in [api/app/retrieval/chroma.py](../api/app/retrieval/chroma.py#L1-L120)
and the shared pin is in [k8s/base/configmap.yaml](../k8s/base/configmap.yaml#L5-L13).
Without that guardrail, the API would embed queries in a different vector space,
return apparently valid but semantically meaningless nearest neighbors, and
produce no obvious error.

## 7. The failure that cost more than an hour

The deployment initially failed because the fixture embedding image was not
available inside Kind; the pod tried Docker Hub, entered `ImagePullBackOff`, and
the indexer could not connect to `ollama-fixture:8081`. The first apparent
symptom was API readiness/Ingress HTTP 503, which initially suggested an API or
LLM problem. The decisive evidence was the pod event and the indexer's refused
connection, followed by the successful fixture readiness and completed indexing
steps in the CD workflow. The fixes are now explicit in
[.github/workflows/cd.yml](../.github/workflows/cd.yml#L250-L420): build/load the
fixture image, wait for its health endpoint, wait for the indexer Job, and retain
failure diagnostics. The later successful run is
[CD run 42486743569](https://github.com/NovweraAmjad/Rahbar/actions/runs/42486743569).
