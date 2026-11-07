# Indexing and retrieval pipeline

<!-- STARTER-TODO(3.1, 3.3, 5.2): fill each section with your numbers, then delete this comment. -->

## Extraction
PDFs are extracted page by page with pdfplumber; pages without a text layer are
OCR'd with pytesseract when its Tesseract runtime is available. UTF-8 text and
Markdown files become one page. Source filename and 1-based page number are
retained. Unsupported or unreadable inputs are logged and produce an empty
document.

Which documents needed OCR, one before-and-after example of a table, what could not be recovered.

## Cleaning
Line endings and tabs are normalized; control characters and repeated blank
lines are removed, and ordinary line whitespace is collapsed. Headings,
paragraph breaks, Unicode punctuation, and numbered references are retained.
Pipe-delimited and visibly aligned table rows keep their internal spacing.

## Chunking
Pages are processed in order and split at Markdown heading boundaries so each
chunk keeps a single section and page citation. Text is packed up to 400
whitespace-delimited tokens by default, preferring paragraph, line, and sentence
boundaries; overlong spans fall back to word boundaries. Consecutive chunks
overlap by 50 tokens within a page and heading section. `CHUNK_SIZE` and
`CHUNK_OVERLAP` can override these defaults; size must be positive and overlap
must be non-negative and smaller than size. This uses whitespace token counts,
not the embedding model's tokenizer, and does not infer non-Markdown headings.

Completed chunk-tuning run `rt3-114805-10405` compared all three profiles
against the same 15-question evaluation set:

| Profile | Size / overlap (tokens) | Chunks stored | Recall@3 | Recall@5 | Citation provenance validity | Gold-evidence relevance | Must-abstain accuracy | Failed questions | Evaluation gate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Small | 200 / 25 | 650 | 66.67% | 75.00% | 100.00% | 66.67% | 100.00% | 8 / 15 | Failed |
| Current | 400 / 50 | 440 | 83.33% | 83.33% | 100.00% | 87.50% | 100.00% | 7 / 15 | Failed |
| Large | 800 / 100 | 355 | 75.00% | 75.00% | 100.00% | 85.71% | 100.00% | 6 / 15 | Failed |

The 400/50 current profile is selected from these measurements because it had
the strongest Recall@5 and gold-evidence relevance. All three profiles
completed, but every profile failed the overall evaluation gate; this
comparison is not evidence that the quality gate passed.

## Embedding
Chunks are embedded with Ollama's batched `POST /api/embed` endpoint, using
`EMBED_MODEL` (default `nomic-embed-text`) and `OLLAMA_URL` from the shared
runtime configuration. The default model produces 768-dimensional vectors;
response counts, numeric finite values, and consistent dimensions are validated
before vectors are returned to storage. Input is sent in batches of 32. Empty
input returns no vectors. Connection errors, API errors (including missing
models), and malformed responses fail indexing with a descriptive error; no
placeholder vectors are generated. The API reads the same `EMBED_MODEL` setting
when embedding retrieval queries.

## Storage
The indexer connects to the configured Chroma host, port, and collection. It
creates the collection with cosine distance and records `embed_model` and vector
`dimension`; existing collections with incompatible or missing metadata are
rejected. Validated records are upserted in batches of 100, with duplicate IDs
resolved by keeping the last input record. The indexer prepares all extracted
documents, chunks, and embeddings before storage starts. It removes stale IDs
only after every upsert batch succeeds. Thus extraction, chunking, and embedding
failures leave the current index untouched; a write failure leaves stale IDs
in place. Chroma does not provide an atomic collection swap here, so a storage
failure after some successful batches can leave a mixture of old and new records.

## Integrated run
`python -m pipeline.run` discovers supported corpus files using `_index.json`
metadata when present, otherwise Markdown front matter, then executes extract,
clean, chunk, embed, and remote Chroma storage in that order. The CLI logs each
stage and exits nonzero on discovery, extraction, embedding, or storage failures.
A corpus with no documents, extraction with no pages, or a run that produces no
chunks is rejected before the existing index is changed. The `--dummy` mode
remains separate and writes the starter's fixed dummy index.

## Semantic cache
Thresholds you tried, the false hit that ruled one out, the one you shipped.

## Evaluation method
Recall@k measures whether a retrieved passage in the top k matches the gold
evidence using the evaluator's source and page matching rule, with section
checked when provided. Citation provenance validity and gold-evidence
relevance are distinct: provenance checks whether each citation matches an
actual retrieved reference (source and page, and section when available);
gold-evidence relevance checks whether citations also match the question's
expected gold evidence. A validly sourced citation can therefore be irrelevant
to the expected answer. In run `rt3-114805-10405`, provenance validity was
100% for all profiles, while gold-evidence relevance ranged from 66.67% to
87.50%.
