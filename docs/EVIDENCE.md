# Submission evidence audit

This document records what is verifiable from the repository and GitHub at the
time of the final audit. It does not invent evidence that was not committed or
observed.

| Submission item | Status | Evidence |
| --- | --- | --- |
| Repository URL | Verified | `https://github.com/NovweraAmjad/Rahbar` |
| Successful CD run | Verified | [run 42486743569](https://github.com/NovweraAmjad/Rahbar/actions/runs/42486743569), commit `43923d0a75d51fe16b78836a3fb6d4662bbc72d7` |
| Test/evaluation gate | Verified for CD fixture gate | Successful `Tests and fixture evaluation gate` job in run 42486743569 |
| SHA-tagged Docker images | Build/push verified | Successful `Build and push SHA-tagged images` job in run 42486743569 |
| Docker Hub image links | **Missing** | The repository does not record the resolved Docker Hub username/repository URLs; collect the three public links and verify the SHA tag |
| Ingress smoke test | Verified for CD run | Successful `Deploy and smoke-test ephemeral kind` job in run 42486743569 |
| Full-corpus results and baseline | Verified as committed artifacts | `eval/results.json` and `eval/baseline.json` |
| Branch protection and blocked-merge screenshots | **Missing** | No repository evidence file or screenshot is committed |
| `kubectl top`, endpoints, and HPA snapshots | **Missing** | No deployment evidence files are committed |
| Deployment screenshot in README | **Missing** | README has an architecture diagram but no screenshot artifact |
| Scanned-PDF OCR interpretation | Partial | Image-only property is verified; OCR runtime/output is not present |
| Long-definition measurement | Partial | Candidate Student Handbook section is identified; measured definition/chunk evidence is absent |
| Assignment exact 100/no-overlap experiment | Missing | The saved Stage B experiment uses 200/25, not the illustrative 100/0 perturbation |

## Readiness verdict

The implementation and latest CD pipeline are substantially complete, but the
repository is not fully submission-ready. The remaining evidence gaps above,
the incomplete OCR/version interpretation, the missing measured storage
amplification, and the non-passing committed full-corpus evaluation are
potential grading deductions. Collect the missing evidence, resolve or explicitly
accept the corpus limitations, and rerun `python scripts/check_submission.py`
before submitting.
