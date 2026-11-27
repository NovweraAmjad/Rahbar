# Minikube base deployment

The base uses the locally loaded `rahbar-api:dev`, `rahbar-indexer:dev`, and
`rahbar-ui:dev` images. Build and load them with `make images`. Start Minikube
with the ingress addon enabled (`make cluster` does this), and make sure Ollama
on the host is listening on `0.0.0.0:11434` with `llama3.2:3b` and
`nomic-embed-text` available.

The indexer image does not contain the corpus. Fetch Batch A into `corpus/raw`
and keep Batch B under `corpus/batch-b`:

```powershell
python corpus\fetch.py
```

In another PowerShell terminal, keep this mount command running while the Job
indexes the corpus:

```powershell
minikube mount "$(Resolve-Path .\corpus):/mnt/rahbar-corpus"
```

Only after the mount is active, apply the base. This creates the one-shot
indexer Job along with the other resources:

```powershell
kubectl apply -k k8s/base
kubectl get pods,services,ingress,pvc -n rahbar
kubectl logs -n rahbar job/indexer -f
```

The Job mounts the host directory read-only at `/corpus`; the indexer reads
`_index.json` and the documents there. Indexer state, quarantine, verification
reports, and version archives are written under `/var/lib/rahbar-data` on the
`rahbar-indexer-data` PVC. The state directory is separate from the read-only
corpus mount, so reindex Jobs can persist state without modifying source files.
The CronJob and one-shot Job share this PVC; do not run concurrent indexer Jobs.
A completed Job is not rerun by another
`kubectl apply`; delete `job/indexer` before reapplying if a fresh indexing run
is needed. The Chroma collection and Redis data are stored on PVCs.

Failed documents are copied to the persistent quarantine directory and checked
against the source SHA-256 before indexing is refused. When the source mount is
writable, the verified copy is followed by removal of the source; on the
read-only `/corpus` mount, the source is retained and the report records that
disposition. Superseded documents are first copied and verified in the
persistent archive. Use the report's paths to recover a document; do not delete
quarantine or archive files until the corresponding review/restore is complete.
Exact and near-duplicate comparisons use only documents that passed all four
verification checks, so a rejected or superseded version cannot cause a current
version to be rejected as its duplicate. Restoring an archived version marks
the active same-family version superseded in Chroma and indexer state, updates
the manifest flags, and reindexes the restored version.

The `reindex` CronJob runs weekly on Mondays at 03:00 cluster time. Keep
`minikube mount "$(Resolve-Path .\corpus):/mnt/rahbar-corpus"` running
continuously for scheduled runs; if the host mount is unavailable at schedule
time, the Job cannot read the corpus and will fail. Ensure `corpus/raw` and
`corpus/batch-b` are
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
