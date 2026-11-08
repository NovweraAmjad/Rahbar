# AI usage disclosure

This project was developed with an AI assistant using Copilot SDK in VS Code.
The assistant helped inspect repository files, reason about failure logs, draft
small application/test/workflow changes, and improve documentation. The human
author reviewed the proposed changes, selected the fixes, and ran the repository
tests, Ruff, mypy, YAML validation, the submission checker, and `git diff --check`.

The AI-assisted changes were not accepted as authoritative output: generated
answers and patches were checked against the source, tests, workflow logs, and
the assignment requirements. In particular, the q04 fix was validated against
the retrieved June 2026 evidence and regression tests; CD changes were checked
against the actual failed and successful run logs; and claims in the documentation
are labelled unverified where no repository evidence exists.

No private credentials, API keys, or third-party confidential data were supplied
to the assistant. The repository's GitHub Secrets and Docker Hub token remain
outside the source tree.
