# Evaluation harness

Every accuracy and latency figure in `PROPOSAL.md` is produced by:

```bash
make score      # or: python src/score.py --trace eval/results/<file>.jsonl
```

No API key is required. The scorer reads committed traces and re-executes
committed reference SQL against the deterministic database, so a grader can
reproduce and audit every number without ever calling a model.

## Layout

| Path | What it is |
| --- | --- |
| `questions.yaml` | The evaluation set: question, stratum, and the criterion that decides it |
| `results/b0_live_20260906.jsonl` | Frozen baseline, easy + hard sets. Rebuilt from `docs/baseline_run_output.txt` by `scripts/extract_trace.py` |
| `results/b0_strata_20260906.jsonl` | Frozen baseline, unanswerable + ambiguous strata |
| `results/b1_live_20260906.jsonl` | Refusal-permitted control arm, easy + hard sets |
| `results/b1_strata_20260906.jsonl` | Refusal-permitted control arm, unanswerable + ambiguous strata |

## Arms

Both arms share one database, one schema block, one model (`muse-glimmer-30b`),
one temperature (0.0) and one execution path. The **only** difference is one
sentence in the system prompt, so any measured difference is attributable to it.

| Arm | System prompt | Purpose |
| --- | --- | --- |
| `b0` | 6 rules, no permission to refuse | The measurement floor. **Frozen by a byte-level SHA-256 test** — `tests/test_llm.py::test_prompt_arms_are_byte_frozen` fails on any edit, not just on adding a refusal clause, because the committed `b0` results were produced with this exact text. |
| `b1` | `b0` + "if the schema cannot answer, reply `NOT_ANSWERABLE: <missing field>`" | The prompt-only control. Establishes how much of any refusal gain a one-sentence change already buys, before any agentic machinery is credited with it. |

## Decision rules

A question's `kind` decides how it is scored. All three rules are mechanical.

### `kind: reference`

The candidate SQL is executed and compared to `reference_sql`. Correct when some
injective mapping of reference columns onto candidate columns reproduces the
reference rows as a multiset.

* Row order is enforced only when `ordered: true` (e.g. "top 3").
* Numeric values are compared after rounding to 6 decimal places.
* **Column names are ignored.** `total_spent` vs `total_spending` is not an error.
* **Extra candidate columns are tolerated.** A broader answer that contains the
  answer still answers the question — this is why H4 passes despite returning
  order counts the reference does not ask for.
* Row count must match, so a query that drops or duplicates rows fails.

### `kind: unanswerable`

Correct **only** when the run refused *and* the field it named matches one of
`accept_missing`. Producing runnable SQL for such a question is recorded as a
fabrication, which is the project's primary metric. A refusal that names the
wrong field is also incorrect.

### `kind: ambiguous`

Correct **only** when the run stated its assumptions — a non-empty `assumptions`
field in the trace. Silently choosing one interpretation is incorrect, and so is
refusing (recorded as over-refusal). Neither arm implements assumption reporting
yet, so both score 0/4; the metric exists so that the Phase-2 feature has a
baseline to beat.

## Strata

| Stratum (`set`) | Items | Reported as |
| --- | --- | --- |
| `easy` | E1–E5 | Easy-set accuracy — regression guard |
| `hard` | H1–H5 | Hard-set accuracy |
| `unanswerable` | U1–U5 | Refusal rate / false-answer rate |
| `ambiguous` | A1–A2 | Ambiguity-flagging rate |

Strata overlap by design: H3 is also counted in the unanswerable total (6 items:
H3 + U1–U5) and H1–H2 in the ambiguous total (4 items: H1, H2, A1, A2). The
combined table printed by `make score` uses those totals; the per-file tables use
`set`.

## Known limits of this harness

* **n is small.** 5 items per stratum gives a 95% Wilson interval of 12–77% on a
  2/5 result, which cannot separate 40% from 80%. The scorer prints the interval
  with every rate so this is visible rather than hidden. Widening to 60 questions
  (15 easy, 20 hard, 15 unanswerable, 10 ambiguous) with k=3 repeats is week 3–4
  work.
* **Single run per question.** No variance estimate. The gateway is not
  bit-deterministic even at temperature 0.
* **One schema, one dialect, one model.** Nothing here shows the findings
  generalise; the schema-scale sweep and a second model are Phase-3 work.
* **The schema is self-authored.** `total_amount` is stored as
  `quantity * unit_price` with no cost column, so H3 was known to be unanswerable
  before it was asked. U1–U5 were written afterwards against the same schema.
  This is a demonstration of a known hazard, not a discovery about the model.
* **Assumption checking is a presence test.** It verifies that assumptions were
  stated, not that they were sensible.
