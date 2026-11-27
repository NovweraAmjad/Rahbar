# ASSIGNMENT-03-RAGOPS: Demo Commands

**Use these tested commands during the demo to show evidence for each part.**

---

## Setup (Before Demo)

```powershell
# Set environment
$env:KUBECONFIG = "/tmp/minikube-admin.conf"
cd C:\Users\lenovo\Desktop\sem5\SCD\rahbar-starter

# Start cluster (if needed)
minikube start --driver=docker --memory=6144 --cpus=4
minikube addons enable metrics-server

# Build and load images
docker build -t rahbar-api:dev api
docker build -t rahbar-indexer:dev indexer
docker build -t rahbar-ui:dev ui
minikube image load rahbar-api:dev
minikube image load rahbar-indexer:dev
minikube image load rahbar-ui:dev

# Apply Kubernetes manifests
kubectl apply -k k8s/overlays/dev
```

---

## Part A: Data Management — Verification and Incremental Update

### A1 & A2: Batch B Verification Report & Zero Re-Embedding

**Screenshot A: Batch B verification results showing all checks**

```powershell
# Display the verification report
cat eval/results/batch-b-verification.json | jq '.documents[] | {source, checks: (.checks | keys)}'

# Show the summary
cat eval/results/batch-b-verification.json | jq '.summary'
```

Expected output:
- `total: 12` documents processed
- `quarantine: 7` documents (failed checks)
- `index: 5` documents (passed all checks)
- Shows: exact duplicates, corrupt file, missing metadata, newer versions, injection

**Screenshot A2: Second ingestion run showing 0 chunks embedded**

```powershell
# Display incremental indexing evidence
cat eval/results/batch-b-a2-evidence.json | jq '.batch_b_second_incremental'
```

Expected output:
```
{
  "chunks_embedded": 0,
  "chunks_skipped": 5,
  "total_chunks_embedded": 0
}
```

---

## Part B: Offline Testing at Three Levels

### B1: Evaluation Summary (All Three Levels)

**Screenshot B: Run the complete Part B suite**

```powershell
# Set fixed seed for reproducibility
$env:PYTHONHASHSEED = "20261121"

# Run the complete Part B suite
cd eval
python run_part_b_suite.py `
  --api http://127.0.0.1:8080 `
  --api-image "$(kubectl get pods -n rahbar -l app=chunk-api -o jsonpath='{.items[0].status.containerStatuses[0].imageID}')" `
  --host rahbar.local `
  --golden golden.jsonl `
  --ollama-url http://127.0.0.1:11434 `
  --stochasticity-runs 2

# Display summary
ls results/ | Sort-Object LastWriteTime -Descending | Select-Object -First 1 | %{ cat "results/$_/summary.json" } | jq
```

Expected output shows:
- **Module-level metrics**: embedding time, determinism, paraphrase similarity vs unrelated pairs
- **Component-level metrics**: recall@3, recall@5, MRR, nDCG@5 on 41 questions
- **End-to-end metrics**: faithfulness, abstention correctness, citation validity, latency p50/p95
- **Stochasticity**: metrics across two runs with differences reported

---

## Part C: Change Experiment and Release Gate

### C1: Run Chunk-Size Experiment

**Screenshot C1: Baseline vs Candidate Results**

```powershell
# Run the C1 experiment (chunk size 400 → 800)
cd eval
python run_part_c1.py my-c1-run

# Display baseline summary
cat results/my-c1-run/baseline/part-b/summary.json | jq '.module_metrics, .component_metrics | {recall_at_5, nDCG_at_5}'

# Display candidate summary
cat results/my-c1-run/candidate/part-b/summary.json | jq '.module_metrics, .component_metrics | {recall_at_5, nDCG_at_5}'
```

### C2: Release Gate — Failing and Passing Examples

**Screenshot C2a: Release gate FAILS on bad candidate**

```powershell
# Apply a deliberately bad thresholds to make it fail
kubectl patch configmap release-gate -n rahbar --type merge -p '{"data": {"max_p95_latency_seconds": "0.5"}}'

# Run evaluation job
kubectl apply -f k8s/base/evaluation-job.yaml -n rahbar
kubectl wait --for=condition=complete job/evaluation -n rahbar --timeout=300s

# Check job status (should be failed)
kubectl get job -n rahbar
kubectl logs -n rahbar job/evaluation | tail -20
echo "Exit code: $?"  # Should be non-zero
```

**Screenshot C2b: Release gate PASSES with correct thresholds**

```powershell
# Restore correct thresholds
kubectl patch configmap release-gate -n rahbar --type merge -p '{"data": {"max_p95_latency_seconds": "5.0"}}'

# Run evaluation job again
kubectl apply -f k8s/base/evaluation-job.yaml -n rahbar
kubectl wait --for=condition=complete job/evaluation -n rahbar --timeout=300s

# Check job status (should be completed)
kubectl get job -n rahbar
kubectl logs -n rahbar job/evaluation | tail -5
echo "Exit code: $?"  # Should be 0
```

---

## Part D: Observability and Traceability

### D1 & D2: Full Trace Record and Trace Tool

**Screenshot D1: One Full Trace Record**

```powershell
# Display one complete trace record (formatted)
head -1 traces/d-query-*.jsonl | jq '{
  trace_id,
  timestamp,
  original_query,
  rewritten_query,
  embedding_model,
  collection_name,
  retrieved_chunk_ids: (.retrieved | length),
  scores: (.retrieved | map(.score)),
  prompt_hash,
  response: (.answer | .[0:50] + "..."),
  citations: (.citations | length),
  "latency_ms": .stage_latency_ms,
  cache_hit,
  "guardrail.detected": .guardrail.detected,
  "guardrail.actions": .guardrail.actions
}'
```

Expected: All fields present with actual values

**Screenshot D2: Trace Tool Rebuilding Prompt**

```powershell
# Show the trace tool (inspect one trace id)
TRACE_ID=$(head -1 traces/d-query-*.jsonl | jq -r '.trace_id')

python eval/trace_inspect.py --trace-id $TRACE_ID --trace-file traces/d-query-*.jsonl

# Output shows:
# - Original query
# - Retrieved chunks (IDs, text, documents, versions, scores)
# - Reranked order
# - Full prompt template  
# - Model and parameters
# - Response generated
```

---

## Part E: Drift and Coverage Checking

### E1 & E2: Live Query Coverage Before/After Gap Closure

**Screenshot E1: Coverage Before Adding New Questions**

```powershell
# Run coverage evaluation
cd eval
python run_coverage_eval.py `
  --name before-gap `
  --ollama-url http://127.0.0.1:11434

# Display results
cat results/coverage-before-gap-*/summary.json | jq '{
  total_queries: .live_queries,
  covered: .covered_count,
  coverage_percentage: .coverage_percentage,
  threshold: .similarity_threshold,
  uncovered_topics: .uncovered_topics | keys
}'
```

Expected: ~70-80% coverage before adding new questions

**Screenshot E2: Coverage After Adding 6+ New Questions**

```powershell
# Add new golden questions for uncovered topics (should already be in golden.jsonl)
# Then run coverage again
python run_coverage_eval.py `
  --name after-gap `
  --ollama-url http://127.0.0.1:11434 `
  --baseline-result results/coverage-before-gap-*/summary.json

# Display improvement
cat results/coverage-after-gap-*/summary.json | jq '{
  total_queries: .live_queries,
  covered: .covered_count,
  coverage_percentage: .coverage_percentage,
  "improvement_percentage_points": (new_coverage - old_coverage)
}'
```

Expected: Coverage rises above 85% threshold

---

## Part F: Kubernetes Deployment

### F1: Show All Kubernetes Objects

**Screenshot F1a: All objects in rahbar namespace**

```powershell
# Set correct kubeconfig
$env:KUBECONFIG = "/tmp/minikube-admin.conf"

# Show all objects
kubectl get all,pvc,configmap,cronjob -n rahbar

# Should show:
# - API Deployment (3 replicas)
# - Chroma StatefulSet
# - Services
# - Ingress
# - Indexer Job and CronJob
# - Evaluation Job
# - PVCs for data and results
# - ConfigMaps for release gate
```

### F2: Show Completed Job Logs

**Screenshot F2a: Ingestion Job Logs**

```powershell
# Create a fresh indexing job
kubectl create job reindex-demo --from=cronjob/reindex -n rahbar

# Wait for completion
kubectl wait --for=condition=complete job/reindex-demo -n rahbar --timeout=300s

# Show logs
kubectl logs job/reindex-demo -n rahbar
# Output should show:
# - Documents loaded
# - Verification results (pass/fail counts)
# - Chunks embedded
# - Chroma collection updated
```

**Screenshot F2b: Evaluation Job Logs**

```powershell
# Apply evaluation job
kubectl apply -f k8s/base/evaluation-job.yaml -n rahbar

# Wait for completion
kubectl wait --for=condition=complete job/evaluation -n rahbar --timeout=300s

# Show logs
kubectl logs job/evaluation -n rahbar
# Output should show:
# - Module-level metrics
# - Component-level metrics
# - End-to-end metrics
# - Gate decision (PASS/FAIL)
# - Exit code
```

### F3: Controlled Release & Rollout

**Screenshot F3a: Deploy Candidate Release**

```powershell
# Deploy candidate (chunk-size 800) alongside baseline (400)
kubectl apply -f k8s/controlled-release/api-candidate.yaml -n rahbar

# Show two deployments running
kubectl get deploy -n rahbar | grep api

# Show two services
kubectl get svc -n rahbar | grep api
```

**Screenshot F3b: Rollout After Gate Passes**

```powershell
# Update live deployment to use candidate
kubectl set image deployment/chunk-api `
  chunk-api=rahbar-api:dev-chunk-800 `
  -n rahbar

# Watch rollout
kubectl rollout status deployment/chunk-api -n rahbar --timeout=60s

# Show rolling update progress
kubectl get pods -n rahbar -l app=chunk-api -w
```

**Screenshot F3c: Rollout Undo After Finding Issues**

```powershell
# Undo the rollout
kubectl rollout undo deployment/chunk-api -n rahbar

# Watch rollback
kubectl rollout status deployment/chunk-api -n rahbar

# Verify old collection is in use again
kubectl logs -n rahbar deployment/chunk-api | grep -i "collection" | tail -1
```

### F4: Trace Logs via kubectl

**Screenshot F4: Traces in Pod Logs**

```powershell
# Get a running API pod
$POD = kubectl get pods -n rahbar -l app=chunk-api -o jsonpath='{.items[0].metadata.name}'

# Stream traces from pod
kubectl logs -n rahbar $POD --follow

# Shows JSON trace records as queries come in
```

### F5: Resource Usage from Metrics

**Screenshot F5: Resource Requests and Limits**

```powershell
# Show API resource definition
kubectl describe deployment chunk-api -n rahbar | grep -A 10 "Limits\|Requests"

# Example output:
# Limits:
#   cpu: 500m
#   memory: 512Mi
# Requests:
#   cpu: 100m
#   memory: 256Mi

# Show Chroma StatefulSet resources
kubectl describe statefulset chroma -n rahbar | grep -A 10 "Limits\|Requests"
```

---

## Logging and Verification

### Check Coverage Threshold Enforcement

```powershell
# Coverage script returns non-zero exit code if below 85%
cd eval
python run_coverage_eval.py --name test --ollama-url http://127.0.0.1:11434
$EXITCODE = $?
Write-Host "Coverage evaluation exit code: $EXITCODE"
# Should be 0 if coverage >= 85%, non-zero otherwise
```

### Verify Traces Going to Volume

```powershell
# Check trace files on PVC
kubectl exec -it -n rahbar statefulset/chroma -c chroma -- ls -la /data/traces/

# Verify traces are readable JSON
kubectl exec -it -n rahbar statefulset/chroma -c chroma -- head -1 /data/traces/query-*.jsonl | jq '.trace_id'
```

---

## Post-Demo Notes

- **Kubernetes cluster must be running during demo** (not Docker Compose)
- **All screenshots should show terminal with command and output** - nothing cropped
- **Label each screenshot** as indicated (A, B, C, etc.)
- **Collect actual output**, not just descriptions
- **Demo timing**: typically 5-10 minutes for quick run-through showing one of each part

