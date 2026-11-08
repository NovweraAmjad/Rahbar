# Indexing and retrieval pipeline

## Extraction

PDFs are extracted page by page with pdfplumber. Pages with no text layer are
sent through the pytesseract path when the Tesseract executable is available;
UTF-8 text and Markdown files become one page. Source filename and 1-based page
number are retained. The scanned Faculty Entrepreneurship and Technical
Start-ups Policy has eight image-only pages, and the 2023 rules PDF has only
limited extractable text. The current environment did not have a Tesseract
runtime, so those OCR results were not produced and must not be presented as
searchable evidence.

Table extraction is deliberately conservative. Text-bearing aligned rows are
kept during cleaning, but no verified before/after OCR table reconstruction is
committed for the image-only PDF. The information-bearing faculty table used as
corpus evidence is the extractable Annual Report 2023-24 table on PDF page 13;
its total row is 735. A future OCR run must preserve a before/after sample and
record what was unrecoverable.

## Cleaning

Line endings and tabs are normalized; control characters and repeated blank
lines are removed, and ordinary line whitespace is collapsed. Headings,
paragraph breaks, Unicode punctuation, numbered references, and the internal
spacing of pipe-delimited or visibly aligned table rows are retained. Repeated
headers and footers are removed only when identified by the extractor; heading
boundaries are not stripped.

## Chunking

Pages are processed in order and split at Markdown heading boundaries so each
chunk keeps a single section and page citation. Text is packed up to 400
whitespace-delimited tokens by default, preferring paragraph, line, and sentence
boundaries. Consecutive chunks overlap by 50 tokens within a page and heading
section. `CHUNK_SIZE` and `CHUNK_OVERLAP` override these defaults with validation.
These are whitespace counts, not the embedding model tokenizer.

Completed run `rt3-114805-10405` compared three profiles against the same
15-question set:

| Profile | Size / overlap | Chunks | Recall@3 | Recall@5 | Citation provenance | Gold relevance | Must-abstain | Gate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Small | 200 / 25 | 650 | 66.67% | 75.00% | 100.00% | 66.67% | 100.00% | Failed |
| Current | 400 / 50 | 440 | 83.33% | 83.33% | 100.00% | 87.50% | 100.00% | Failed |
| Large | 800 / 100 | 355 | 75.00% | 75.00% | 100.00% | 85.71% | 100.00% | Failed |

The 400/50 profile was selected for the strongest Recall@5 and gold relevance.
The saved comparison is evidence of a tuning experiment, not a passing gate.
The exact assignment perturbation of fixed 100-token chunks with no overlap was
not run.

Storage amplification (stored tokens divided by original tokens) was not
measured in a committed run. The chunk counts above must not be substituted for
that ratio; this remains a submission evidence gap.

## Embedding

Chunks are embedded with Ollama's batched `/api/embed` endpoint using
`EMBED_MODEL` and `OLLAMA_URL` from shared runtime configuration. The default
model is `nomic-embed-text` and its vectors are expected to be 768-dimensional.
Counts, finite numeric values, and consistent dimensions are validated before
storage. The API reads the same model setting for query embeddings.

## Storage

The indexer creates a cosine-distance Chroma collection and records
`embed_model` and vector `dimension`. Each record carries `source`, `section`,
`page`, `doc_version`, and `superseded` metadata. Records are upserted in batches
of 100 after extraction, chunking, and embedding succeed; stale IDs are removed
only after all upserts succeed. Existing collections with incompatible metadata
are rejected. Chroma storage and the Redis cache are persistent in the
Kubernetes deployment; see [DEPLOYMENT.md](DEPLOYMENT.md).

## Semantic cache

The optional Redis cache uses cosine similarity at the ConfigMap threshold.
The 20-pair probe in `eval/cache-threshold-runs/20261107T171600/` found one
false hit at 0.90 and none at 0.93, 0.95, or 0.97. The deployed threshold is
0.95. The sample is pair-level evidence only and does not certify production
answer correctness or exercise Redis failure handling.

Follow-ups, abstentions, out-of-scope answers, invalid model responses, and
uncited answers are not cacheable. Indexing invalidates keys for the configured
prefix and collection before changing Chroma; invalidation failure stops index
writes.

## Evaluation method

Recall@k matches gold source/page evidence in the first k results, with section
matching when supplied. Citation provenance checks that every citation matches
an actually retrieved reference; gold-evidence relevance additionally checks
the question's expected evidence. The committed evaluation results report
Recall@3 and Recall@5, citation provenance, gold relevance, and abstention
correctness by category. The gate requires no request failures, 100% abstention
correctness, at least 95% citation provenance, and no Recall@5 drop greater than
five percentage points from the baseline.
