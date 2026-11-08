# Minikube base deployment

The base uses the locally loaded `rahbar-api:dev`, `rahbar-indexer:dev`, and
`rahbar-ui:dev` images. Build and load them with `make images`. Start Minikube
with the ingress addon enabled (`make cluster` does this), and make sure Ollama
on the host is listening on `0.0.0.0:11434` with `llama3.2:3b` and
`nomic-embed-text` available.

The indexer image does not contain the PDFs. Fetch them into `corpus/raw` first:

```powershell
python corpus\fetch.py
```

In another PowerShell terminal, keep this mount command running while the Job
indexes the corpus:

```powershell
minikube mount "$(Resolve-Path .\corpus\raw):/mnt/rahbar-corpus"
```

Only after the mount is active, apply the base. This creates the one-shot
indexer Job along with the other resources:

```powershell
kubectl apply -k k8s/base
kubectl get pods,services,ingress,pvc -n rahbar
kubectl logs -n rahbar job/indexer -f
```

The Job mounts the host directory read-only at `/corpus`; the indexer reads
`_index.json` and the documents there. A completed Job is not rerun by another
`kubectl apply`; delete `job/indexer` before reapplying if a fresh indexing run
is needed. The Chroma collection and Redis data are stored on PVCs.

The `reindex` CronJob runs weekly on Mondays at 03:00 cluster time. Keep
`minikube mount "$(Resolve-Path .\corpus\raw):/mnt/rahbar-corpus"` running
continuously for scheduled runs; if the host mount is unavailable at schedule
time, the Job cannot read the corpus and will fail. Ensure `corpus/raw` is
updated on the host before the scheduled run.

NetworkPolicies require a CNI that enforces them. The inspected active cluster
uses `kindnet`, so these policies must not be assumed to provide isolation
there. Use Minikube with Calico (as configured by `make cluster`) before
applying them in an environment where enforcement is required.

The Ingress expects host `rahbar.local` and the Minikube NGINX ingress class.
On Windows with the Docker driver, run `minikube tunnel` in another terminal if
needed, and map `rahbar.local` to the Minikube ingress address in the hosts
file. The API routes retain the `/api` prefix.

The API probes use `/healthz` for startup and liveness and `/readyz` for
readiness. Dedicated ServiceAccounts are assigned to each workload, with API
tokens disabled. Pod security contexts require non-root execution and a
read-only root filesystem. NetworkPolicies deny ingress and egress by default,
then allow DNS and only the documented application paths.
