# ADR 0004: LangGraph for the state machine, retry cap of 3

**Status:** accepted for Phase 2

## Context

The agentic phase needs a control flow of gate → generate → validate → execute →
verify → answer, with bounded repair. A plain `while` loop would also work.

## Decision

Write the logic as plain functions first, port to LangGraph once it is settled and
tested, and cap repair attempts at 3.

**Why LangGraph:** typed graph state and node-level toggles make the per-node
ablation (ADR 0002) a configuration flag rather than a code branch, which is what
the evaluation plan requires. It also gives per-node tracing for free, feeding the
JSONL traces the scorer already consumes.

**Why not framework-first:** the framework is not the contribution, and a week
lost to framework churn is a week not spent on evaluation. Logic first, port
second.

**Why a cap of 3:** the failure being repaired is a database error, and the
baseline produced **zero** SQL errors in 17 runs, so the repair loop has almost no
headroom on this schema. Its purpose is bounding worst-case latency, not
recovering accuracy. With a p50 already at 17.2s, a cap of 5 risks a 90s worst
case for a mechanism the evidence says will rarely fire; 2 gives no room for the
error-then-fix-then-verify sequence. 3 is the smallest cap that permits that
sequence, and it terminates in an explicit "I could not answer this" state rather
than a silent failure.

## Consequences

The retry cap is a latency control, and is reported as such. If the ablation shows
the repair node never fires on the 60-question set, the honest conclusion is to
remove it — and that removal is a finding, not a failure.
