# ADR 0002: Run the prompt-only control arm before building the agent

**Status:** accepted, executed 2026-09-06

## Context

The project's thesis was that an agentic loop is needed to stop the system
fabricating answers to questions the schema cannot support. The baseline `b0`
prompt never tells the model it is *allowed* to refuse, so a measured 0% refusal
rate would have been evidence that the model was never asked — not evidence that
a single call cannot refuse.

Had the LangGraph agent been built first and then measured at, say, 90% refusal,
the gain would have been attributed to the architecture, and the first question at
review would have been: "what happens if you add one sentence to the prompt?"

## Decision

Define arms that differ by exactly one thing, and run the cheap ones first:

| Arm | Change from previous | Status |
| --- | --- | --- |
| `b0` | — (frozen floor) | measured |
| `b1` | + one sentence granting permission to refuse | measured |
| `b2` | + self-consistency, k=5, result-set voting | week 4 |
| `a1` | + answerability gate, validator, repair, verifier | weeks 5–6 |

`b0` is frozen: `tests/test_llm.py::test_b0_never_grants_permission_to_refuse`
fails if anyone edits it, because the committed `b0` traces would then no longer
describe the code that produced them.

## Outcome

`b1` refused 6/6 unanswerable questions, naming the missing field each time,
against 0/6 for `b0`, with no regression on the easy set (5/5 → 5/5). **The
refusal gain requires no agent.** It also cost one over-refusal: H2, an
answerable-but-ambiguous question, was refused.

## Consequences

The agentic phase can no longer be justified by refusal, and the proposal says so.
Its remaining justification is what neither arm does: flagging ambiguity (0/4 for
both), verifying a result against the question, and bounded repair. Per-node
ablation is therefore mandatory, not optional — each node must show the accuracy
and latency it earns.
