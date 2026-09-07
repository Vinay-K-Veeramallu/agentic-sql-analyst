# ADR 0003: Inject the whole schema; do not retrieve it

**Status:** accepted for this schema size, with a stated threshold

## Context

Text-to-SQL systems at scale retrieve a relevant subset of the schema. The
proposal excludes RAG from scope, and an exclusion that is asserted rather than
argued reads as avoidance.

## Decision

Inject the entire schema into every prompt while the schema is small, and state
the threshold at which that stops being right.

This database is 3 tables and renders to ~240 prompt tokens (measured: mean 237
prompt tokens per call across the instrumented runs). Retrieval over 3 tables
cannot improve recall — it is already 100% — and it introduces a new failure mode:
retrieving the wrong tables and making an answerable question unanswerable.

**Threshold:** schema retrieval becomes necessary above roughly 50 tables, or when
rendered DDL exceeds ~8k tokens, whichever comes first. Below that, flat injection
is strictly better because it cannot drop a needed table.

## Consequences

Phase 3 tests the threshold rather than assuming it: the schema-scale sweep clones
the schema to 30 and 300 tables and measures accuracy against schema size. The
point where flat injection degrades is the point where RAG earns its place, and it
is reported as a curve rather than a claim.
