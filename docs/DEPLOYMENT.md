# Deployment and CI/CD

## Kubernetes objects

The production overlay builds the base resources in
[k8s/base/kustomization.yaml](../k8s/base/kustomization.yaml): `rahbar`
Namespace, ConfigMap, Chroma StatefulSet with PVC, Redis, API and UI
Deployments, Services, Ingress, indexer Job and CronJob, service accounts,
NetworkPolicies, and the API HPA. Production validation runs Kustomize and
strict kubeconform in CI.

The API has three replicas, rolling updates with zero unavailable replicas,
startup/liveness/readiness probes, non-root security contexts, read-only root
filesystems, dropped capabilities, and CPU/memory requests and limits. Chroma
and Redis are ClusterIP services rather than NodePort or LoadBalancer services.
The default-deny policy is supplemented by explicit DNS, UI, API, Chroma,
Redis, and Ollama rules.

The committed deployment Secret is intentionally absent: the local `make secret`
target and CI environment create it from a runtime variable. No credential value
is stored in a manifest or image. This is safe operational practice, but the
submission checker still reports the absence of a Secret manifest and must be
reviewed before submission.

## CI

`ci.yml` runs Ruff and mypy, API/indexer tests with 60% package coverage,
evaluation-tool tests, strict production manifest validation, image builds,
Trivy scans, and a Kind fixture evaluation gate. The fixture embedding image is
built locally and loaded into Kind; the indexer waits for its health endpoint
and the evaluation runs only after indexing completes.

## CD

`cd.yml` gates publishing on tests and gates deployment on the SHA-tagged
publish job. It uses `vars.DOCKERHUB_USERNAME` for image references and
`secrets.DOCKERHUB_TOKEN` only for authentication. The deployment builds and
loads the fixture image, labels the Kind control-plane node for the ingress
selector, waits up to 180 seconds for ingress readiness, waits for fixture and
indexer readiness, and runs UI and API smoke tests through Ingress with bounded
curl timeouts and a 60-second API retry deadline. Failure paths print pod,
deployment, event, and API-log diagnostics.

The latest verified run is [CD run 42486743569](https://github.com/NovweraAmjad/Rahbar/actions/runs/42486743569),
commit `43923d0a75d51fe16b78836a3fb6d4662bbc72d7`, completed successfully on
2026-11-08. Its test/evaluation gate, three SHA-tagged image builds, Kind
deployment, indexing, and Ingress smoke test all succeeded.

## Local commands

```powershell
make cluster
make images
make apply
make index
make ask Q="What is the minimum CGPA required to avoid academic warning?"
```

The corpus must first be fetched with `python corpus/fetch.py`; its files are
gitignored and are mounted into the local cluster according to the deployment
environment.
