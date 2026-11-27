# ASSIGNMENT-03 DELIVERABLES INDEX

**Complete Assignment-03 RAGOps Implementation**  
**Status: PRODUCTION READY** ✅  
**Date: 2026-11-27**

---

## 📋 Deliverable Documents

### 1. **ASSIGNMENT-03-REPORT.md** (PRIMARY SUBMISSION)
   - **8 pages, 592 lines, 33KB**
   - **Purpose:** Final comprehensive report meeting all Part G requirements
   - **Contains:**
     - Executive summary
     - Figure 3 RAGOps stage mapping table
     - Table 2 qualities with measurements, thresholds, evidence
     - Complete results from Parts A–F with actual numbers
     - Part C release gate decision (APPROVED)
     - 6 short-answer questions (a–f) with grounded answers
     - Contribution table (placeholders for group members)
     - AI assistance statement (2–3 lines)
     - References section
     - **12 screenshot placeholders** with exact commands, expected outputs, filenames
   - **Action Required:** Update group member names (line 3–4, section 8)
   - **Action Required:** Capture 12 screenshots using commands in Appendix

### 2. **ASSIGNMENT-03-VERIFICATION.md** (REQUIREMENT CHECKLIST)
   - **10.8KB, 109 lines**
   - **Purpose:** Complete A1–F5 requirement audit
   - **Contains:**
     - 40+ requirements mapped (A1→F5)
     - Status indicator (✅ complete / 🔄 in progress)
     - Implementation file for each requirement
     - Test/demo command to verify
     - Evidence path (where committed)
     - Notes on requirements, evidence, demo notes
   - **Use For:** Reference during presentation/demo

### 3. **ASSIGNMENT-03-DEMO-COMMANDS.md** (COPY-PASTE READY)
   - **10.9KB, 314 lines**
   - **Purpose:** All tested commands for live demo
   - **Contains:**
     - Commands for all parts A–F
     - Expected output for each command
     - Screenshot labels (A1–F2)
     - Timing notes (how long each command takes)
   - **Use For:** Copy-paste during live demonstration

### 4. **ASSIGNMENT-03-COMPLETION-SUMMARY.md** (THIS DOCUMENT'S COMPANION)
   - **12.6KB, 256 lines**
   - **Purpose:** Overview of what's complete, what user needs to do
   - **Contains:**
     - Detailed completion status for each part
     - List of committed evidence files
     - What still needs manual work (screenshots, member names)
     - How to verify everything works
     - Next steps to submit

### 5. **README.md** (UPDATED)
   - **Modified existing file**
   - **Added:**
     - Assignment 03 RAGOps section with quick-reference table
     - Links to all three deliverable documents
     - Part A–F main commands at a glance

---

## 📁 Evidence Files (All Committed)

### Part A: Verification & Incremental Indexing
- `eval/results/batch-b-verification.json` — 12 docs: 7 quarantined, 5 indexed
- `eval/results/batch-b-a2-evidence.json` — Incremental proof (5→0 chunks)
- `eval/results/batch-b-run/quarantine/` — 7 quarantined documents
- Verification checks: quality, completeness, recency, uniqueness, injection

### Part B: Offline Testing
- `eval/golden.jsonl` — 41 questions (24 answerable, 5 must-abstain, 12 hard)
- `eval/golden.pre-part-e.jsonl` — 30-question baseline (SHA256 locked)
- `eval/results/module-embedding-20261121T122510Z/` — Embedding quality tests
- `eval/results/component-retrieval-20261121T134645Z/` — Retrieval metrics
- `eval/results/end-to-end-20261121T130052Z/` — Answer quality (recall, faithfulness, latency)
- `eval/results/part-b-stochasticity-20261122T122424Z/` — Reproducibility tests

### Part C: Release Gate
- `eval/release-gate.yaml` — Thresholds (max -2pp recall, min 70% faithfulness, max 5s p95)
- `eval/results/c1-20261122t1458/` — Baseline (400-token chunks)
- `eval/results/c1-20261122t1733/` — Candidate (800-token chunks)
- Release decision: **✅ APPROVED** (all metrics within bounds)

### Part D: Traceability
- `traces/d-query-20261122T1230Z.jsonl` — 50+ query traces (20+ fields each)
- `eval/results/d-trace-summary-20261122T131209Z.json` — Aggregated trace metrics
- `eval/trace_inspect.py` — Tool to reconstruct prompt/execution from trace ID

### Part E: Drift & Coverage
- `eval/live_queries.yaml` — 40 queries (28 in-domain, 12 out-of-domain)
- `eval/results/drift-part-e-complete/` — Batch B drift comparison
- `eval/golden-dataset-counts.json` — Coverage distribution by topic
- Drift results: Recall -0.43pp, Faithfulness -1.5pp (acceptable)

### Part F: Kubernetes
- `k8s/base/kustomization.yaml` — Base manifests
- `k8s/overlays/dev/kustomization.yaml` — **Dev overlay (fixed, was missing)**
- `k8s/overlays/prod/kustomization.yaml` — Prod overlay
- `k8s/controlled-release/` — Blue-green deployment manifests
- PVCs persist across pod restarts; data preserved

---

## ✅ Implementation Status by Part

| Part | Requirement | Status | Evidence | Notes |
|------|-------------|--------|----------|-------|
| **A1** | Verification framework | ✅ COMPLETE | batch-b-verification.json | 4 checks + injection detection |
| **A2** | Incremental indexing | ✅ COMPLETE | batch-b-a2-evidence.json | 5→0 chunks on second run |
| **B1** | Golden dataset ≥25q, 50% answerable | ✅ COMPLETE | eval/golden.jsonl | 41q: 24 answerable, 5 must-abstain |
| **B2** | Module tests (embedding) | ✅ COMPLETE | module-embedding-* | Determinism ✓, discrimination ✓ |
| **B3** | Component tests (retrieval) | ✅ COMPLETE | component-retrieval-* | Recall@5=0.90, nDCG=0.88 |
| **B4** | End-to-end tests (answers) | ✅ COMPLETE | end-to-end-* | Faithfulness=90%, abstention=100%, p95=2.4s |
| **B5** | Stochasticity testing | ✅ COMPLETE | part-b-stochasticity-* | Max variance 0.69pp (acceptable) |
| **C1** | Release gate thresholds | ✅ COMPLETE | release-gate.yaml | -2pp, 70%, 5s defined |
| **C2** | Chunk-size experiment | ✅ COMPLETE | c1-baseline/, c1-candidate/ | 400→800: all metrics within gate |
| **C3** | Release decision | ✅ COMPLETE | ASSIGNMENT-03-REPORT.md | ✅ APPROVED (justified) |
| **D1** | Trace schema (20+ fields) | ✅ COMPLETE | d-query-20261122T1230Z.jsonl | All required fields present |
| **D2** | 50+ production traces | ✅ COMPLETE | d-query-20261122T1230Z.jsonl | 50+ trace records |
| **D3** | Injection defense | ✅ COMPLETE | trace inspection demo | Injected clause redacted before LLM |
| **D4** | Root-cause trace tool | ✅ COMPLETE | eval/trace_inspect.py | Reconstructs prompt, execution, timing |
| **D5** | Citation validity | ✅ COMPLETE | trace inspection | 97.6% citations verified in chunks |
| **E1** | 40 live queries (split) | ✅ COMPLETE | eval/live_queries.yaml | 28 in-domain, 12 out-of-domain |
| **E2** | Drift measurement | ✅ COMPLETE | drift-part-e-complete/ | Recall -0.43pp, faithfulness -1.5pp |
| **E3** | Coverage analysis | ✅ COMPLETE | golden-dataset-counts.json | No topic >25% |
| **E4** | Pre-part-e golden locked | ✅ COMPLETE | golden.pre-part-e.jsonl | SHA256 hash verified |
| **E5** | Drift within thresholds | ✅ COMPLETE | drift results | -0.43pp < -2.0pp threshold ✓ |
| **F1** | K8s namespace rahbar | ✅ COMPLETE | k8s/ manifests applied | Pods running, PVCs bound |
| **F2** | StatefulSets + PVCs | ✅ COMPLETE | Chroma-0, indexer-0 running | Persistence verified |
| **F3** | Single-writer constraint | ✅ COMPLETE | indexer pipeline design | Only Job writes to Chroma |
| **F4** | Overlay structure | ✅ COMPLETE | k8s/overlays/dev + prod | Both properly configured |
| **F5** | Controlled release | ✅ COMPLETE | k8s/controlled-release/ | Blue-green + canary split setup |

---

## 🎯 Screenshots Required (12 total)

Each screenshot command is documented in **ASSIGNMENT-03-REPORT.md Appendix**.  
Run each command, capture output, label as shown below.

| Label | Topic | Expected Output | Time Required |
|-------|-------|-----------------|---|
| **A1** | Batch B Verification | 12 docs, 7 quarantined, 5 indexed, checks breakdown | 1 min |
| **A2** | Incremental Indexing | Run 1: 5 embedded, Run 2: 0 embedded | 1 min |
| **B1** | Golden Set Composition | 41 total, 24 answerable, 5 must-abstain, distribution | 1 min |
| **B2** | Retrieval & Answer Metrics | Recall≈0.90, MRR≈0.85, Faithfulness≈90%, p95≈2400ms | 1 min |
| **C1** | Baseline Chunk-Size 400 | Recall≈0.90, Faithfulness≈90%, latency≈2400ms | 1 min |
| **C2** | Candidate Chunk-Size 800 | Recall≈0.89, Faithfulness≈89%, latency≈2500ms (APPROVED) | 1 min |
| **D1** | Trace Structure | trace_id, query, 5 chunks with scores, prompt_hash, response | 2 min |
| **D2** | Injection Defense | "Injection detected" → "redacted" → clean question to LLM | 1 min |
| **E1** | Live Query Set | 40 queries split: 28 in-domain, 12 out-of-domain examples | 1 min |
| **E2** | Coverage Distribution | Before/after topic distribution (no category >25%) | 1 min |
| **F1** | K8s Pods & PVCs | rahbar-chroma-0, rahbar-indexer-0 RUNNING, PVCs Bound | 1 min |
| **F2** | Blue-Green Deployment | Blue/green services, 50% traffic split capability | 1 min |

**Total Time:** 5–10 minutes to capture all 12 screenshots

---

## 🚀 Submission Checklist

Before submitting:

- [ ] Read `ASSIGNMENT-03-REPORT.md` sections 1–10
- [ ] Update group member names (line 3–4, section 8)
- [ ] Run each command in `ASSIGNMENT-03-DEMO-COMMANDS.md`
- [ ] Capture 12 screenshots (Appendix placeholders provide exact commands)
- [ ] Insert screenshots into report Appendix
- [ ] Verify report is 8 pages or less (excluding screenshots)
- [ ] Check `ASSIGNMENT-03-VERIFICATION.md` for coverage
- [ ] (Optional) Add deployment proof if required:
  - GitHub Actions run URL
  - Docker Hub repository URLs
  - Branch protection export
- [ ] Git commit (optional: `git commit -m "Assignment-03: Final report + deliverables"`)
- [ ] Submit via Learning Management System

---

## 📞 Troubleshooting Common Issues

### "Golden set validation test failing"
→ Run: `python eval/validate_golden_set.py`  
→ Check: All 41 questions have correct schema (must_abstain not unanswerable)

### "Traces not visible"
→ Run: `ls -la traces/`  
→ Expected: `d-query-20261122T1230Z.jsonl` present (50+ lines)

### "K8s pods not running"
→ Set: `export KUBECONFIG=/tmp/minikube-admin.conf`  
→ Run: `kubectl get pods -n rahbar`

### "Chunk-size results missing"
→ Check: `eval/results/c1-20261122t1733/` exists  
→ Run: `cat eval/results/c1-20261122t1733/summary.json | jq`

### "Part F dev overlay still missing"
→ Verify: `k8s/overlays/dev/kustomization.yaml` exists (should be present, was fixed)

---

## 📚 Reference Guide

- **Assignment Specification:** [ASSIGNMENT-03-RAGOPS.md](ASSIGNMENT-03-RAGOPS.md.md)
- **Implementation Code:** `indexer/`, `api/`, `eval/`, `k8s/`
- **Result Data:** `eval/results/`, `traces/`, `.indexer-state/`
- **Kubernetes:** `k8s/base/`, `k8s/overlays/`, `k8s/controlled-release/`

---

**Generated:** 2026-11-27 15:00 UTC  
**Git Repo:** NovweraAmjad/Rahbar (dev branch)  
**Status:** Ready for final presentation & submission
