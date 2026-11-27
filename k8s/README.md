# Assignment 03 Part F: Kubernetes RAGOps

All commands below are run from the repository root. The base namespace is
`rahbar`; the live collection is `rahbar`. The candidate uses a separate
collection and never replaces the live collection's data.

## Fresh Minikube deployment

Start a Calico cluster, load the local images, and keep Ollama available on the
host with `nomic-embed-text` and `llama3.2:3b`:

```powershell
minikube start --profile minikube --driver=docker --cni=calico --memory=6144 --cpus=4
minikube addons enable ingress
minikube addons enable metrics-server
docker build -t rahbar-api:dev api
docker build -t rahbar-indexer:dev indexer
docker build -t rahbar-ui:dev ui
docker build -f eval/Dockerfile.part-c3 -t rahbar-eval:dev .
minikube image load rahbar-api:dev
minikube image load rahbar-indexer:dev
minikube image load rahbar-ui:dev
minikube image load rahbar-eval:dev
```

The checked-in API manifests pin the Minikube-loaded image by manifest digest.
After rebuilding an API image, list its actual Minikube manifest digest and
update that immutable reference in the base and controlled-release manifests:

```powershell
minikube ssh "sudo ctr -n k8s.io images list | grep rahbar-api"
minikube ssh "sudo ctr -n k8s.io images tag docker.io/library/rahbar-api:dev docker.io/library/rahbar-api@sha256:<manifest-digest>"
```

The following mount must stay running. Its files are copied by the ingestion
Job init container into the persistent data-lake directory:

```powershell
minikube mount "$(Resolve-Path .\corpus):/mnt/rahbar-corpus"
```

In another terminal, apply and verify the base:

```powershell
kubectl apply -k k8s/base
kubectl rollout status statefulset/chroma -n rahbar --timeout=5m
kubectl rollout status deployment/api -n rahbar --timeout=5m
kubectl rollout status deployment/ui -n rahbar --timeout=5m
kubectl get all,pvc,configmap,cronjob,job -n rahbar
```

## Ingestion, verification, and persistence

The one-shot `indexer` Job runs with `--incremental`. Its init container copies
the host corpus mount into `/var/lib/rahbar-data/corpus` on
`rahbar-indexer-data`; the main container writes only verified chunks to
`rahbar` in Chroma. Quarantine, verification reports, archives, traces, and
incremental state use persistent subdirectories on the same PVC.

```powershell
kubectl delete job indexer -n rahbar --ignore-not-found
kubectl apply -f k8s/base/indexer-job.yaml
kubectl wait --for=condition=complete job/indexer -n rahbar --timeout=20m
kubectl logs job/indexer -n rahbar --all-containers=true
```

An unchanged second run proves incremental behavior:

```powershell
kubectl delete job indexer -n rahbar
kubectl apply -f k8s/base/indexer-job.yaml
kubectl wait --for=condition=complete job/indexer -n rahbar --timeout=20m
kubectl logs job/indexer -n rahbar
```

The expected final log has `chunks_embedded: 0` and the number of skipped chunks.
The `reindex` CronJob uses the same init container, PVC, collection, and
`concurrencyPolicy: Forbid`; do not start another ingestion Job concurrently.

## Full evaluation Job and gate

`rahbar-release-gate` is the only quality-threshold source used by the
evaluation Job. `part-b-evaluation` runs the complete Part B module, component,
and end-to-end suite twice, writes to the `rahbar-evaluation-results` PVC, and
exits with status 1 when the measured gate fails:

```powershell
kubectl delete job part-b-evaluation -n rahbar --ignore-not-found
kubectl apply -f k8s/base/evaluation-job.yaml
kubectl wait --for=condition=complete job/part-b-evaluation -n rahbar --timeout=45m
kubectl logs job/part-b-evaluation -n rahbar
kubectl get job part-b-evaluation -n rahbar -o jsonpath='{.status.conditions[0].type}{" exit="}{.status.conditions[0].reason}{"\n"}'
```

The first log records are bounded preflight checks for API `/healthz`, Chroma
`/api/v2/heartbeat`, and Ollama `/api/tags`. Each connection has a 5-second
timeout. A policy/DNS/endpoint failure therefore terminates the Job with exit
code 2 and names the dependency that could not be reached; the Job also has a
90-minute `activeDeadlineSeconds` ceiling.

If preflight reports a timeout, verify the two directions of the connection:

```powershell
kubectl get networkpolicy -n rahbar
kubectl get endpoints api chroma -n rahbar
kubectl exec -n rahbar <evaluation-pod> -- python -c "import socket; print(socket.getaddrinfo('api',8000)); print(socket.getaddrinfo('chroma',8000))"
kubectl exec -n rahbar <evaluation-pod> -- python -c "import httpx; print(httpx.get('http://api:8000/healthz',timeout=5).status_code); print(httpx.get('http://chroma:8000/api/v2/heartbeat',timeout=5).status_code)"
```

`allow-api-ingress` and `allow-chroma-ingress` must select both
`app=part-b-evaluation`; `allow-evaluation-egress` must permit those
destinations. A ready Service endpoint without those ingress rules still
produces a silent packet drop under the default-deny policy.

If the gate fails, `kubectl wait --for=condition=complete` times out because
the Job has a Failed condition. This is the intended nonzero behavior; use:

```powershell
kubectl get job part-b-evaluation -n rahbar
kubectl logs job/part-b-evaluation -n rahbar
```

To prove threshold changes do not require an image rebuild, edit only the
ConfigMap and recreate the Job. For example, set
`min_faithfulness_percentage: 100.0` to force a measured candidate to fail:

```powershell
kubectl edit configmap rahbar-release-gate -n rahbar
kubectl delete job part-b-evaluation -n rahbar
kubectl apply -f k8s/base/evaluation-job.yaml
```

Restore the checked-in threshold with
`kubectl apply -f k8s/base/release-gate-configmap.yaml`.

## Candidate evaluation, blocked release, promotion, and rollback

Validate candidate isolation, create it, index its collection, and run its
gate-aware evaluation Job:

```powershell
python eval/run_controlled_release.py validate
kubectl apply -f k8s/controlled-release/candidate-configmap.yaml
kubectl apply -f k8s/controlled-release/evaluation-results-pvc.yaml
kubectl apply -f k8s/controlled-release/candidate-api.yaml
kubectl apply -f k8s/controlled-release/networkpolicy-candidate.yaml
kubectl delete job indexer-candidate -n rahbar --ignore-not-found
kubectl apply -f k8s/controlled-release/candidate-indexer-job.yaml
kubectl wait --for=condition=complete job/indexer-candidate -n rahbar --timeout=20m
kubectl rollout status deployment/api-candidate -n rahbar --timeout=5m
kubectl delete job part-b-candidate -n rahbar --ignore-not-found
kubectl apply -f k8s/controlled-release/part-b-candidate-job.yaml
kubectl get job part-b-candidate -n rahbar -w
kubectl logs job/part-b-candidate -n rahbar
```

Copy the generated candidate summary and run the checked-in local gate to create
the promotion artifact. A failed gate is preserved and blocks promotion:

```powershell
$pod = kubectl get pods -n rahbar -l job-name=part-b-candidate -o jsonpath="{.items[0].metadata.name}"
kubectl exec -n rahbar $pod -- find /results/part-b-candidate -name summary.json
kubectl cp "rahbar/$pod`:/results/part-b-candidate/<suite>/summary.json" eval/results/controlled-release/candidate-summary.json
python eval/run_release_gate.py `
  --baseline eval/results/c1s/baseline/part-b/part-b-stochasticity-20261122T110731Z/summary.json `
  --candidate eval/results/controlled-release/candidate-summary.json `
  --config eval/release-gate.yaml `
  --out eval/results/controlled-release `
  --name candidate-gate
python eval/run_controlled_release.py promote `
  --gate-report eval/results/controlled-release/candidate-gate.json `
  --candidate-result eval/results/controlled-release/candidate-summary.json `
  --record eval/results/controlled-release/promotion.json `
  --context minikube
```

The last command must exit 1 and leave Deployment `api` unchanged for a FAIL
report. For a measured PASS report, the same command performs a RollingUpdate
with `maxUnavailable: 0` and records the previous revision/configuration.

Rollback to the recorded revision, then send a fresh query and verify that its
trace names the previous collection and embedding version:

```powershell
python eval/run_controlled_release.py rollback `
  --record eval/results/controlled-release/promotion.json `
  --context minikube
kubectl port-forward -n rahbar service/api 18080:8000
python eval/run_controlled_release.py verify-rollback-trace `
  --record eval/results/controlled-release/promotion.json `
  --api http://127.0.0.1:18080 `
  --query "What attendance is required?" `
  --context minikube
```

## Traces and metrics

The API writes one compact JSON record per query to stdout and to
`/var/lib/rahbar-data/traces/queries.jsonl` on the persistent PVC.

```powershell
kubectl port-forward -n rahbar service/api 18080:8000
curl.exe -sS -H "Content-Type: application/json" `
  -d '{"question":"What minimum CGPA avoids academic warning?"}' `
  http://127.0.0.1:18080/api/ask
kubectl logs -n rahbar -l app=api --tail=100 | Select-String query_trace
$pod = kubectl get pods -n rahbar -l app=api -o jsonpath='{.items[0].metadata.name}'
kubectl cp "rahbar/$pod`:/var/lib/rahbar-data/traces/queries.jsonl" traces/kubernetes-queries.jsonl
```

Inspect an ID or summarize a UTC window:

```powershell
python eval/trace_inspect.py <trace-id> --trace-file traces/kubernetes-queries.jsonl
python eval/summarize_traces.py traces/kubernetes-queries.jsonl `
  --since 2026-11-23T00:00:00Z `
  --until 2026-11-24T00:00:00Z `
  --output eval/results/part-f-live-20261123/trace-summary.json
curl.exe -sS http://127.0.0.1:18080/metrics
```

## Resource evidence

Metrics Server is enabled by the setup commands. Collect actual values while
the API, Chroma, indexer, and evaluation workloads are running:

```powershell
kubectl top nodes
kubectl top pods -n rahbar
kubectl get pvc -n rahbar
kubectl get all,pvc,configmap,cronjob,job -n rahbar
```

Current requests and limits are deliberately distinct by workload:

- API/candidate API: 100m CPU / 256Mi request, 500m / 512Mi limit.
- Chroma: 100m / 256Mi request, 1 CPU / 1Gi limit.
- Indexer/candidate indexer: 500m / 1Gi request, 2 CPU / 3Gi limit.
- Evaluation: 250m / 512Mi request, 1 CPU / 2Gi limit.

Use the captured `kubectl top` output, rather than these limits, as the actual
measurement evidence.
