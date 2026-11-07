# Live golden evaluation

`golden.yaml` contains 15 grounded questions: 8 ordinary answerable questions,
2 version-sensitive questions, 2 exact-term questions, 2 must-abstain questions,
and 1 prompt-injection question. The answers are editorial references for review;
the runner does not compare generated wording because answer correctness still
requires human review.

## Gold evidence and metrics

Each answerable case names the indexed source filename and PDF page, plus the
section when that metadata can be stated reliably. Retrieval matches require an
exact source filename; if gold evidence specifies a page, the page must also
match; if it specifies a section, section text must match after whitespace and
case normalization. A document-level result does not match gold evidence that
specifies a page. Recall@k is the fraction of gold evidence items present in the
first *k* retrieved references, aggregated by evidence item overall and by
category.

Citation metrics distinguish provenance from relevance:

- **Citation provenance validity** is the percentage of emitted citations whose
  source and page match a reference retrieved for that request. Sections must
  also match when both citation and retrieved-reference section metadata are
  available. This is the assignment's citation-validity metric and its 95%
  gate.
- **Gold-evidence relevance** is the percentage of emitted citations that are
  provenance-valid and also match a gold evidence item under the strict source,
  page, and supplied-section rule above. It is an additional diagnostic; it
  does not replace or relax provenance validation.

Both percentages are also recorded per question, with each citation's
provenance and gold-relevance result and its matching retrieved/gold metadata.
Percentages are unavailable when there are no emitted citations. A must-abstain
case is correct only when `answered_from_context` is false and no citations are
returned. Request failures are reported, never counted as successes; metrics
that need a complete set are marked unavailable rather than treated as zero.

The pass/fail detail checks answerability, retrieval of all gold evidence by
rank 5, and 100% gold-evidence relevance for answerable cases; it checks the abstention
rule for must-abstain and prompt-injection cases. Generated answer correctness
is not an automatic metric. CI gates on no request errors, 100% must-abstain
correctness, at least 95% citation provenance validity, and no Recall@5 drop
greater than 5 percentage points against an available baseline.

## Run against Minikube Ingress

Start or confirm the local ingress controller and forward its HTTP service:

```powershell
kubectl -n ingress-nginx port-forward service/ingress-nginx-controller 8080:80
```

In another terminal, run from the repository root:

```powershell
python eval/run_eval.py
```

The runner defaults to `http://localhost:8080` and sends `Host: rahbar.local`,
as required by the Ingress rule. The endpoint and host can be overridden:

```sh
RAHBAR_EVAL_BASE_URL=http://localhost:8080 python eval/run_eval.py
```

On PowerShell, set the environment variable before running Python:

```powershell
$env:RAHBAR_EVAL_BASE_URL = "http://localhost:8080"
python eval/run_eval.py
```

`RAHBAR_EVAL_HOST` or `--host` changes the Host header. Use `--api` to override
the URL directly, `--golden path/to/golden.yaml` to select another dataset, and
`--out path/to/results.json` to select the output file. For example:

```powershell
python eval/run_eval.py --api http://localhost:8080 --host rahbar.local --golden eval/golden.yaml --out eval/results.json
```

The full response and request ID for each case, failures, metrics, and gate
result are saved to `eval/results.json`. The runner creates
`eval/baseline.json` after a run with no request failures, only if that baseline
does not already exist. The snapshot records whether the evaluation gate
passed; a non-passing snapshot is still a record of measured results, not
evidence that the API passed. Later runs compare Recall@5 against the existing
baseline and never overwrite it.

Review a later result before refreshing the baseline. Preserve the old snapshot,
then create a candidate at a separate path:

```powershell
Copy-Item eval/baseline.json eval/baseline-approved-previous.json
python eval/run_eval.py --baseline eval/baseline-candidate.json
```

After reviewing the candidate metrics and question-level details, replace the
approved baseline explicitly only if the gate passes and the run is accepted;
retain the archived copy. Results and baselines contain no API credentials; do
not put credentials in the URL or golden file.
