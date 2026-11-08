# RAHBAR

<!-- STARTER-TODO(10): replace this file before you submit. It must contain: a one-paragraph
     problem statement, a CI badge, an architecture diagram, the exact apply order, the path from
     a clean clone to an answered question, a table of the API's endpoints, and a screenshot.
     STARTER.md explains the starter kit; this file explains YOUR system. -->

## Corpus source

The public FAST-NUCES PDF corpus is described in [docs/CORPUS.md](docs/CORPUS.md).
Third-party PDFs are not committed; `corpus/MANIFEST.md` records their original
URLs and verified SHA-256 checksums, and `corpus/fetch.py` downloads them into
the gitignored `corpus/raw/` directory from `corpus/sources.yaml`.
