# ADR 0004: Pluggable generation provider and PII boundary

## Status
Accepted

## Context

The API must support a replaceable generation provider without coupling retrieval
to a vendor SDK. User questions and retrieved corpus text may contain sensitive
content, while provider credentials must never be committed.

## Decision

Select the provider through `LLM_PROVIDER` behind the `LLMProvider` interface,
with Ollama, hosted, and stub implementations. Keep API keys in a runtime
Kubernetes Secret or GitHub Secret, never in manifests, images, URLs, or logs.
Send only the validated question and delimited retrieved evidence to the
provider, validate structured output, strip reasoning before display, and
return a safe fallback on generation/validation failure.

## Consequences

Provider changes remain localized and offline tests use the stub. Secrets are
operational inputs rather than repository artifacts, so a deployment must create
them explicitly. This repository does not claim to provide full PII detection or
redaction; that limitation should be addressed before using private corpora.
