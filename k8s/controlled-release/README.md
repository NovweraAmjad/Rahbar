# Assignment 03 Part C3 controlled release

These manifests are intentionally outside `k8s/base` and are not applied by
`kubectl apply -k k8s/base`. The workflow is opt-in. The implementation and
validation commands below do not change a cluster; only the explicitly marked
`promote` and `rollback` commands modify the live Deployment.

## What is isolated

- The live API remains behind Service `api`.
- The candidate uses Deployment `api-candidate` and Service `api-candidate`.
- `rahbar-candidate-config` pins a distinct Chroma collection
  (`rahbar-candidate-c1`), embedding model, LLM/provider, chunk size/overlap,
  and a distinct semantic-cache prefix.
- `indexer-candidate` indexes the corpus into that candidate collection.
- `part-b-candidate` runs the complete Part B suite against
  `http://api-candidate:8000` and writes results to a PVC.
- The live API Deployment already has `RollingUpdate` with `maxUnavailable: 0`;
  the promotion helper validates this in the manifests and the live Deployment,
  then includes the same setting in the promotion patch.

## Preflight and candidate evaluation

Use the cluster context and corpus mount expected by your deployment. Build and
load the evaluation image for the selected local cluster, as well as the
candidate API/indexer images:

```powershell
docker build -f eval/Dockerfile.part-c3 -t rahbar-eval:dev .
docker build -t rahbar-api:dev api
docker build -t rahbar-indexer:dev indexer
```

For Minikube, load each image with `minikube image load <image>`. For Kind,
load them with `kind load docker-image <image> --name <cluster-name>`.
Keep the corpus mounted at `/mnt/rahbar-corpus` on the Kubernetes node.

Validate without contacting or changing Kubernetes:

```powershell
python eval/run_controlled_release.py validate
```

After confirming the target context and namespace are the intended deployment,
apply the candidate configuration and workload, then run the index and Part B
Jobs in order:

```powershell
kubectl apply -f k8s/controlled-release/candidate-configmap.yaml
kubectl apply -f k8s/controlled-release/evaluation-results-pvc.yaml
kubectl apply -f k8s/controlled-release/candidate-api.yaml
kubectl apply -f k8s/controlled-release/networkpolicy-candidate.yaml
kubectl create -f k8s/controlled-release/candidate-indexer-job.yaml
kubectl wait --for=condition=complete job/indexer-candidate -n rahbar --timeout=2h
kubectl logs job/indexer-candidate -n rahbar
kubectl rollout status deployment/api-candidate -n rahbar --timeout=5m
kubectl create -f k8s/controlled-release/part-b-candidate-job.yaml
kubectl wait --for=condition=complete job/part-b-candidate -n rahbar --timeout=2h
kubectl logs job/part-b-candidate -n rahbar
```

The fixed Job names are single-run names. For another experiment, preserve the
previous results, use a fresh candidate collection and unique Job names, and
use a new immutable candidate ConfigMap name before creating new Jobs. Do not
delete/reuse an existing collection for a new configuration.

Copy the completed Part B summary from its Job pod to the repository. The PVC
retains the full individual results:

```powershell
$pod = kubectl get pods -n rahbar -l job-name=part-b-candidate -o jsonpath="{.items[0].metadata.name}"
kubectl exec -n rahbar $pod -- find /results/part-b-candidate -name summary.json
kubectl cp "rahbar/$pod:/results/part-b-candidate/<suite-directory>/summary.json" eval/results/controlled-release/candidate-summary.json
```

Apply C2 using the approved baseline summary and that exact copied candidate
summary. Keep both report files:

```powershell
python eval/run_release_gate.py `
  --baseline eval/results/<approved-run>/baseline/part-b/part-b-stochasticity-<run>/summary.json `
  --candidate eval/results/controlled-release/candidate-summary.json `
  --config eval/release-gate.yaml `
  --out eval/results/controlled-release `
  --name candidate-gate
```

## Promotion gate and rollout record

Promotion is blocked unless the gate JSON says PASS, every configured check
passed, the candidate result path is the exact file evaluated by the gate, and
the helper recomputes the gate from the recorded baseline/candidate result files
and thresholds. It also requires an Available candidate Deployment and a
successfully completed Part B Job. The helper records the prior revision,
image, collection and model configuration before patching the live Deployment.

Only after reviewing a passing C2 report, explicitly run:

```powershell
python eval/run_controlled_release.py promote `
  --gate-report eval/results/controlled-release/candidate-gate.json `
  --candidate-result eval/results/controlled-release/candidate-summary.json `
  --record eval/results/controlled-release/promotion.json `
  --context <approved-context>
```

The helper changes only the live Deployment pod template. It uses the candidate
image/configuration, a `RollingUpdate` strategy with `maxUnavailable: 0`, and
records the output of `kubectl rollout status`. It does not rewrite the live
ConfigMap, candidate Job, or Ingress.

## Rollback and fresh-query trace proof

Rollback is explicit and uses the revision saved before promotion:

```powershell
python eval/run_controlled_release.py rollback `
  --record eval/results/controlled-release/promotion.json `
  --context <approved-context>
kubectl port-forward -n rahbar service/api 18080:8000
```

In another terminal, ask a fresh query. The helper finds that request's
`query_trace` JSON line and verifies the recorded `collection_name` and
`embedding_model_version` equal the previous values in the promotion record:

```powershell
python eval/run_controlled_release.py verify-rollback-trace `
  --record eval/results/controlled-release/promotion.json `
  --api http://127.0.0.1:18080 `
  --query "What attendance is required?" `
  --context <approved-context>
```

The trace verification JSON and promotion/rollback status are saved under
`eval/results/controlled-release/`. No rollout/rollback evidence is claimed
until these commands are run against the intended cluster and their output is
preserved.
