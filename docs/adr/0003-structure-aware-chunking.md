# ADR 0003: Use structure-aware overlapping chunks

## Status
Accepted

## Context

Fixed windows can split headings, clauses, and citations. The assignment requires
structure-aware chunking with overlap and measured tuning rather than an arbitrary
size.

## Decision

Split at headings, then pack paragraphs, lines, and sentences up to 400
whitespace tokens with 50-token overlap. Keep source, section, page, version, and
supersession metadata on every stored chunk. Select 400/50 because the committed
three-profile experiment had the strongest Recall@5 and gold-evidence relevance.

## Consequences

Chunk boundaries preserve citation context but increase storage and embedding
work. The experiment is evidence for the selected profile, not a passing quality
gate; storage amplification still needs a committed measurement.
