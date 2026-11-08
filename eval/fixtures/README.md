# CI fixtures

Three short Markdown documents for the `e2e-eval` job (section 6), so CI can index
a corpus and score recall in a minute instead of six. The institute is fictional.

| File | Why it is here |
| --- | --- |
| `academic-rules-2023.md` | The superseded version (75% attendance, CGPA 1.75, grade cap B) |
| `academic-rules-2026.md` | The current version (80%, 2.00, no cap), plus one planted injection line |
| `fees-2026.md` | Two tables and exact-term numbers |

The hostel topic is missing on purpose: `golden.fixture.yaml` asks about it and
expects the service to decline.

Each file starts with front matter (`doc_version`, `superseded`). The indexer's
`discover()` reads it when there is no `_index.json`, so your extract stage
needs to handle `.md` files as well as PDFs.

CI indexes these documents through the real indexer Job into Chroma and queries
that collection through the real API retrieval path. A small deterministic,
Ollama-compatible lexical embedding service is used only in the ephemeral CI
cluster; its stop-word handling avoids ranking the planted instruction boilerplate
over the attendance evidence. The answer-generation provider is the application's
stub. This keeps fixture evaluation independent of external model services without
replacing indexing, Chroma, retrieval, or `eval/run_eval.py` with mocked assertions.
The CI overlay uses a fixture-only relevance floor of `0.1` because these
deterministic lexical vectors do not share the production embedding model's score
scale; the production ConfigMap remains unchanged.

`quality-target.json` is a declared fixture quality contract, not a measured
historical baseline, and is not the full-corpus `eval/baseline.json`. Its
Recall@5 target is 1.0 because the
five answerable questions each have one annotated evidence section. The
assignment gate's five-percentage-point allowance therefore requires all five
answerable evidence sections in the top five. The same evaluator gate still
requires at least 95% citation provenance validity and 100% must-abstain
correctness. Do not compare fixture metrics to the full-corpus baseline.
