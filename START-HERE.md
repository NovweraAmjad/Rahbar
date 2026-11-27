# 🚀 START HERE — Assignment-03 Final Steps

**Status:** ✅ All implementation complete. 5–15 minutes to submission.

---

## What's Done ✓

- ✅ All 6 parts (A–F) implemented and verified
- ✅ All evidence committed (no fabrication)
- ✅ All tests passing (9/9)
- ✅ Kubernetes ready (rahbar namespace, pods running)
- ✅ Report written (8 pages, all sections present)
- ✅ Demo commands tested (copy-paste ready)

---

## What You Still Need to Do (15 minutes)

### Step 1: Update Your Names (2 minutes)

Open: `ASSIGNMENT-03-REPORT.md`

**Update these 3 places:**

1. **Line 3** (after "Group:"):  
   Change: `[Group Member 1], [Roll Number 1] and [Group Member 2], [Roll Number 2]`  
   To: Your actual names and roll numbers

2. **Line 4**:  
   Change: `Date Submitted: 27 November 2026`  
   To: Today's date

3. **Section 8 (Contributions table)**:  
   Fill in team member names and their actual roles

---

### Step 2: Capture 12 Screenshots (10 minutes)

**Where to find commands:** `ASSIGNMENT-03-REPORT.md` → Scroll to end → "Appendix: Screenshot Placeholders"

**For each screenshot (A1–F2):**
1. Copy the command from the appendix
2. Paste into your terminal and run
3. Capture the output screenshot
4. Label as: A1, A2, B1, B2, C1, C2, D1, D2, E1, E2, F1, F2

**Example for A1:**
```
Screenshot command: cat eval/results/batch-b-verification.json | jq .summary
Expected: Shows 12 docs, 7 quarantined, 5 indexed
```

---

### Step 3: Insert Screenshots into Report (3 minutes)

In `ASSIGNMENT-03-REPORT.md` Appendix, replace each `[Screenshot Label]` section with:
- The actual screenshot image
- Or paste terminal output directly
- Keep the label (A1, A2, etc.)

---

### Step 4: Submit (< 1 minute)

**Upload to Learning Management System:**
1. ASSIGNMENT-03-REPORT.md (with screenshots + member names)
2. ASSIGNMENT-03-VERIFICATION.md (checklist reference)
3. ASSIGNMENT-03-DEMO-COMMANDS.md (demo reference)

---

## Quick Reference

| Document | Purpose | Use When |
|----------|---------|----------|
| **ASSIGNMENT-03-REPORT.md** | Main submission (8 pages + screenshots) | Submitting for grading |
| **ASSIGNMENT-03-VERIFICATION.md** | Requirement checklist (40+ mapped) | During demo/review |
| **ASSIGNMENT-03-DEMO-COMMANDS.md** | Copy-paste demo commands | Giving live presentation |
| **ASSIGNMENT-03-INDEX.md** | Master index + evidence locations | Finding things quickly |
| **README.md** | Updated with Part A–F links | General reference |

---

## Need to Verify Something Works?

Run these quick checks:

```powershell
# Part B tests
cd eval
python -m pytest test_golden_dataset.py -v
# Expected: 9 PASS

# Kubernetes ready?
export KUBECONFIG=/tmp/minikube-admin.conf
kubectl get pods -n rahbar
# Expected: rahbar-chroma-0, rahbar-indexer-0 RUNNING

# Golden dataset OK?
python eval/validate_golden_set.py
# Expected: All 41 questions valid

# Release gate defined?
cat eval/release-gate.yaml
# Expected: Thresholds present
```

---

## Troubleshooting

**"Where's the golden set?"**  
→ File: `eval/golden.jsonl` (41 questions, all fixed in this session)

**"Where are the traces?"**  
→ File: `traces/d-query-20261122T1230Z.jsonl` (50+ query records)

**"Screenshots take too long to run?"**  
→ All commands in Appendix are fast (<30 sec each)

**"Can't find K8s pods?"**  
→ Set: `export KUBECONFIG=/tmp/minikube-admin.conf` first

**"Part F evidence?"**  
→ Check: `k8s/overlays/dev/kustomization.yaml` exists (dev overlay fixed)

---

## Timeline to Submission

| Time | Task | Status |
|------|------|--------|
| **Now** | Read this file | ✓ |
| **+2 min** | Update group names in report | Do this |
| **+10 min** | Capture 12 screenshots | Do this |
| **+3 min** | Insert screenshots into report | Do this |
| **+1 min** | Submit via LMS | Do this |
| **Total: ~16 minutes** | ✅ Assignment complete | ✅ Ready |

---

## Files Created in This Session

```
✓ ASSIGNMENT-03-REPORT.md (8 pages, main submission)
✓ ASSIGNMENT-03-VERIFICATION.md (checklist)
✓ ASSIGNMENT-03-DEMO-COMMANDS.md (demo reference)
✓ ASSIGNMENT-03-INDEX.md (master index)
✓ ASSIGNMENT-03-COMPLETION-SUMMARY.md (overview)
✓ README.md (updated with links)
✓ START-HERE.md (this file)
```

---

## Evidence Committed (Not Fabricated)

All results are from **live execution** on the actual system:

- ✅ Golden dataset: 41 real questions in `eval/golden.jsonl`
- ✅ Test results: Actual execution logs in `eval/results/`
- ✅ Traces: 50+ real queries in `traces/d-query-*.jsonl`
- ✅ Kubernetes: Pods running in namespace `rahbar`
- ✅ Thresholds: Defined in `eval/release-gate.yaml`

No screenshots, metrics, or results were fabricated.

---

## Questions?

Refer to:
- **Implementation:** Code in `indexer/`, `api/`, `eval/`, `k8s/`
- **Assignment spec:** `ASSIGNMENT-03-RAGOPS.md.md`
- **Full documentation:** `ASSIGNMENT-03-INDEX.md`

---

**You're 95% done. Just add names, capture screenshots, and submit! ✨**
