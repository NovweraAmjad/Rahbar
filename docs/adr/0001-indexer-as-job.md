# ADR 0001: Run indexing as a Kubernetes Job

## Status
Accepted

## Context

The API has multiple replicas while Chroma has one persistent writer path.
Indexing during API startup would make every replica repeat extraction and
embedding, race on writes, and couple API availability to a long-running batch
operation.

## Decision

Run indexing in a dedicated `batch/v1 Job`, with a CronJob for scheduled reindexing.
The API Deployment only serves queries. The Job uses the shared ConfigMap,
writes to Chroma, and invalidates the configured Redis cache before changing the
collection.

## Consequences

Deployments can roll out independently of indexing, and a failed index run is
visible as a failed Job rather than a restart loop in every API replica. Operators
must wait for the Job before treating readiness and evaluation as application
success. The Job currently relies on the configured corpus volume and Chroma
service being available.
