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

| Size (tokens) | Overlap (tokens) | recall@5 | Chunks stored |
| --- | --- | --- | --- |

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
How recall@k matches retrieved passages to golden ones (by source and section? by chunk id?).
