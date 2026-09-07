# Capstone Project Proposal

| Field | Response |
| --- | --- |
| **Student name** | Vinay Kumar Veeramallu |
| **Project title** | QueryMind: A Self-Correcting Agentic Text-to-SQL Assistant |
| **Repository / notebook link** | https://github.com/Vinay-K-Veeramallu/agentic-sql-analyst (branch `baseline`) |
| **Configuration location** | `config/settings.yaml`; env vars in `.env.example`; setup in `README.md` §2–3 |

*Every number below is regenerated offline by `make score` from committed traces — no API key needed to verify any of it.*

## 1. Problem Definition

**Task.** Turn a plain-English business question into an executable read-only SQL query, run it against a relational database, and return the result table — or, when the schema cannot answer the question, refuse and name the missing field.

**Intended user and situation.** Non-technical stakeholders — product managers, operations leads, sales analysts — who need a number from a company database, cannot write SQL, do not know the schema, and today file a data-engineering ticket and wait.

**Input.** One natural-language question (e.g. `"Who are the top 3 customers by total spending?"`) plus a read-only SQLite connection. **Output.** The generated SQL, the result table, and an explicit refusal naming the missing field when the schema cannot answer. Summaries and charts are later-phase outputs.

**Success.** Decided per question by `python src/score.py`, never by inspection. An **answerable** question succeeds when its result set matches a hand-written reference query in `eval/questions.yaml` as an unordered row multiset — order enforced only where the question implies it ("top 3"), numbers rounded to 6 decimals, column names ignored, row count required to match. An **unanswerable** question succeeds only when the system refuses and names a genuinely absent field. An **ambiguous** question succeeds only when it answers *and* states its assumption (`eval/README.md`).

**Failure.** A syntax or schema error; an unhandled execution error; a result disagreeing with the reference; a fabricated answer where the data does not exist; an over-refusal of an answerable question; or any attempt to execute a write.

## 2. Motivation and Project Scope

**Why the problem matters.** The analytics bottleneck is a queue of small ad-hoc requests waiting on a data team — but the failure that blocks adoption is not slowness, it is a confidently wrong answer. A table of zeros gets screenshotted into a board deck; an error message does not. This baseline produced exactly that failure in **6 of 6** measured cases (§4).

**Why an agentic approach is reasonable — and where my evidence limits the claim.** The environment supplies free feedback at every step: the schema says which identifiers exist, the engine returns errors, and a result can be checked against the question — the condition under which an act–observe–revise loop pays for itself. But my control arm (§4) shows **refusal does not need an agent**: one sentence of prompt recovered all of it. What a single call still fails at is flagging ambiguity (0/4, both arms) and verifying that a returned table answers the question. This project measures how much agency those two jobs justify — a narrower, more falsifiable claim than "I built an agent."

**Feasibility.** One local 3-table SQLite database: no infrastructure, credentials, or data collection. The harness, scorer and 79 tests exist, so what remains is a bounded state machine over a fixed benchmark.

| In scope this semester | Deliberately out of scope |
| --- | --- |
| Read-only querying of local SQLite; multi-table JOINs | Writes, DDL, cloud warehouses, auth, multi-tenancy |
| Refusal naming the missing field; ambiguity flagging | Schema retrieval / RAG below ~50 tables (ADR 0003) |
| Result verification; repair loop capped at 3 retries | Fine-tuning; unstructured-document RAG |
| Arms `b0`→`b1`→`b2`→`a1` with per-node ablation | Deployment, monitoring, cost controls |
| 60-question benchmark; thin Streamlit demo | Polished UI and chart generation (deferred) |

## 3. Runnable Baseline

**Model, tools, frameworks and libraries.** Python 3.11, standard-library `sqlite3`, `pandas`, and the `openai` client against ASU's OpenCode gateway (model `muse-glimmer-30b`, temperature 0). **No agent framework — that is the point.** A deterministic offline `mock` provider is built in so the pipeline runs with no credentials.

**What the baseline does, step by step** (`src/baseline.py`):

1. Open `data/analytics.db` **read-only**.
2. Introspect every table via `PRAGMA table_info` / `foreign_key_list` into a compact schema block (`src/db.py`).
3. Compose exactly one prompt: system rules + schema + question (`src/llm.py`).
4. Make **one** chat-completion call and take the reply as SQL.
5. Guard the reply — a single read-only `SELECT`/`WITH`, cost-bounded.
6. Execute, print the table, append a JSONL trace. On error it logs and moves on: **there is no retry loop.**

**Read-only is enforced three independent ways**, so no single failure is sufficient: the connection is opened `?mode=ro`; a SQLite **statement authorizer** permits only read actions, so DDL/`ATTACH`/`PRAGMA` are denied by the engine rather than by string matching; and a **progress handler** bounds query cost while rows stream under a flagged cap. The keyword check in front of these produces readable errors only — it is not the security boundary (ADR 0001, pinned by `tests/test_guard.py`).

**Why this is a reasonable starting point.** It is the weakest system that answers the question end to end, so it isolates the one variable this capstone is about. The `b0` prompt is **frozen by a byte-level SHA-256 test** — it cannot be edited without failing the suite and forcing a version bump plus re-measurement (`tests/test_llm.py`). Every later arm shares the same database, schema block, model, temperature, execution path and scorer, so any measured difference is attributable to the single thing that changed. It is an instrument, not a candidate system.

**Files containing the implementation.** `src/baseline.py` (CLI entry point), `src/db.py` (read-only execution, guards, introspection), `src/llm.py` (prompt arms and providers), `src/score.py` (mechanical scorer), `eval/questions.yaml` (reference SQL), `eval/results/*.jsonl` (committed traces), `scripts/seed_db.py` (deterministic seeder), `tests/` (79 tests).

## 4. Test Case and Baseline Output

**Sample input** (`examples/test1.txt`): `Who are the top 3 customers by total spending?` — against `data/analytics.db`, seed 598 (20 customers, 15 products, 300 orders, md5 `d612d894…e7ab`, checked by `make verify-data`). Answering requires joining `orders` to `customers`; the answer is in no single table.

**Expected output.** The reference query for `E1` in `eval/questions.yaml` — `SUM(total_amount)` grouped by customer, ordered descending, limit 3 — yielding **Alice Johnson 10643.33, Bob Smith 10539.88, Laura Wilson 9250.98**.

**Actual baseline output.** Live run against the ASU gateway: **Figure 1** (screenshot). The same rows reproduced offline with no API key: **Figure 4**. The baseline grouped by `customer_id, customer_name` and aliased the sum `total_spending`; it returns identical rows and **passes**, because the matching rule ignores column names. It was then run over the full 17-question set (5 easy, 5 hard, 5 unanswerable, 2 ambiguous; strata overlap, so labels sum to 20 across 17 questions — H1–H2 are both hard and ambiguous, H3 both hard and unanswerable). **Figure 3** shows `make score` regenerating this table:

| Arm | Execution success | Easy | Hard | Unanswerable refused | Ambiguity flagged | Fabrications | p50 (all 17) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `b0` frozen baseline | 17/17 | 5/5 | 2/5 | 0/6 | 0/4 | 6/6 | 23.13s |
| `b1` = `b0` + one sentence | 10/17 (7 refusals) | 5/5 | 3/5 | 6/6 | 0/4 | 0/6 | 11.69s |

**What worked — better than expected.** Zero syntax errors and zero hallucinated identifiers across 17 runs; correct foreign-key joins unprompted; sensible aggregate aliases; a correct `ROW_NUMBER() OVER (PARTITION BY …)` on "the second-most-popular product in each category." The guard never had to fire. Easy set 5/5.

**What did not work — and it reframes the project.** I expected failure on SQL *syntax*, fixable by a retry loop reading engine errors. The measurements refute that: **there were no errors to read.** The real failure is *silent semantic error*. Asked "what is the profit margin per category?", the baseline returned a clean table of zeros (**Figure 2**) — `total_amount` is `quantity × unit_price` and the schema has no cost column, so the expression is identically zero. Nothing errored, so no retry loop would have caught it. It fabricated **6/6** across the unanswerable stratum and flagged ambiguity **0/4**, silently inventing both thresholds for "signed up early but never became high spenders."

**The control arm, and its honest consequence.** Before building anything agentic I ran `b1`: same model, schema and execution path, plus one sentence permitting `NOT_ANSWERABLE: <field>`. It refused **6/6**, naming the right field each time, with no easy-set regression and hard-set accuracy up 2/5 → 3/5 (**Figure 2**, lower panel). **The refusal gain requires no agent.** It also cost one over-refusal, now a tracked metric. This partly falsifies my original thesis, which is why §2 and §6 are written against it (ADR 0002).

**Latency.** Two scopes, not interchangeable. On the original 10 live questions `b0` gives p50 **17.20s** (mean 29.66s, max 121.80s, 6/10 over the 15s target) against `b1`'s 13.26s; across all 17 runs per arm it is **23.13s** against `b1`'s 11.69s — refusals are cheap, so permitting them lowers latency. An 11th `b0` run repeated a question and returned 0.86s against 13.41s for identical SQL — a gateway cache hit, excluded and flagged in the trace. Latency tracks generated tokens, not database work: SQLite returned every result in under 1ms.

**Construction disclosure.** The schema is self-authored and its unanswerable region was known in advance — I designed `total_amount` as `quantity × unit_price` with no cost column, then wrote the other unanswerable items against it. These demonstrate a known hazard rather than a discovery about the model; §6 does not treat them as external validation.

## 5. Reproducibility and Run Instructions

```
git clone https://github.com/Vinay-K-Veeramallu/agentic-sql-analyst.git
cd agentic-sql-analyst && git checkout baseline
make setup && make seed        # venv + pinned deps; build data/analytics.db
make verify-data && make test  # md5 d612d894b6250755906a962edf27e7ab; 79 tests
make demo                      # python src/baseline.py --input examples/test1.txt --provider mock
make score                     # regenerates every number in this document
```

**Dependencies and installation.** `make setup` installs pinned versions — `openai==3.8.0`, `pandas==3.0.5`, `python-dotenv==1.2.3`, `PyYAML==6.0.3`, `numpy==2.4.6`, Python 3.11.14 (`.python-version`). Under two minutes.

**API keys / environment variables.** **None for grading** — `make demo` and `make score` never call a model. Live runs need `OPENCODE_BASE_URL` and `OPENCODE_API_KEY` in `.env` (copy `.env.example`); `--provider openai` takes any OpenAI-compatible key.

**Exact command.** `python src/baseline.py --input examples/test1.txt --provider mock`, or `--provider asu` for a live run; the full set runs via `--questions`.

**Input / output locations.** Input: `examples/test1.txt` (single case) and `eval/questions.yaml` (17 questions). Output: stdout, `logs/baseline_run.log`, and a JSONL trace at `--trace`.

**Known setup limitations.** Needs Python 3.11 and `make` (plain commands in `README.md` §5 otherwise); SQLite only. Live re-execution needs an ASU key a non-ASU grader cannot obtain — hence every live run is committed as JSONL and re-scored offline. The `mock` provider's rules came from `examples/queries.txt`, so its accuracy is meaningless by construction and never reported.

## 6. Initial Evaluation Plan

Arms share one harness, database, model and prompt scaffold, so each delta is attributable to one change: `b0` (floor) → `b1` (+permission to refuse, **done**) → `b2` (+self-consistency, k=5) → `a1` (gate, validator, bounded repair, verifier), then `a1` ablated node by node.

| Metric | `b0` | `b1` | Target `a1` |
| --- | --- | --- | --- |
| **False-answer rate** (primary) — fabrications ÷ unanswerable | 6/6 | 0/6 | ≤1/15 |
| **Ambiguity flagged** — answer states its assumption | 0/4 | 0/4 | ≥75% |
| **Easy accuracy** (regression guard); **hard accuracy** | 5/5; 2/5 | 5/5; 3/5 | 5/5; ≥70% |
| **Over-refusal**; **execution success** | 0; 17/17 | 1; 10/17 | ≤1; ≥98% |
| **Latency p50** (all 17); **tokens/question** (n=7) | 23.13s; 2182 | 11.69s; 1245 | <15s; reported |

**What evidence would show improvement.** `a1` must beat **`b1`, not `b0`**, on the primary metric and on ambiguity flagging, with no easy-set regression and the added latency reported. If `b1` or `b2` matches `a1`, the architecture was unnecessary — and that is what will be reported. Current n cannot settle it (a 2/5 result has a 95% Wilson interval of 12–77%, printed beside every rate), so week 3 widens to 60 questions (15 easy, 20 hard, 15 unanswerable, 10 ambiguous) at k=3.

## 7. Limitations and Next Steps

**Known weaknesses.** Fabricates on unanswerable questions (6/6); never flags ambiguity (0/4); p50 17.20s (original 10) and 23.13s (all 17) against a 15s target; no verification that a result answers the question; 5–6 items per stratum, single runs, wide intervals; one schema, dialect and model; `b1` over-refuses; no CI yet. *Not* a weakness, contrary to expectation: SQL syntax and schema grounding, zero errors in 17 runs.

**Expected failure cases.** Relative time with no anchor ("last quarter"); contested definitions ("active customer"); `INNER` vs `LEFT` silently dropping customers with no orders; unexplained empty results; prompt injection via stored data; schemas exceeding the prompt window.

**Next phase.** *Weeks 3–4:* 60 questions at k=3, the `b2` arm, CI. *Weeks 5–6:* LangGraph state machine (gate → generate → validate → execute → verify), repair capped at 3, per-node ablation, assumption reporting. *Weeks 7–8:* schema-scale sweep (3 → 300 tables), a second model, Streamlit demo.

**Risks.** Control arm further undermines the thesis (re-scope to ambiguity and verification, report the negative result); small n (k=3 over 60 questions); latency compounding (the validator adds no model call); over-refusal (tracked metric, easy set as regression gate); toy-schema artefacts (scale sweep, second model, §4 disclosure). **What I may need:** sustained ASU gateway quota (~1,800 calls) and a day to write the remaining reference queries.

## Figures

*Evidence appendix; the proposal body is the two pages above. Raw transcripts are committed under `docs/transcripts/`.*

**Figure 1** — `docs/screenshot.png`. Live baseline run (§4): `python src/baseline.py --input examples/test1.txt --provider asu`.

**Figure 2** — `docs/figures/fig2_fabrication_vs_refusal.png`. `python scripts/show_case.py --id H3`: arm `b0` fabricates a table of zeros for profit margin; `b1`, same model and question, refuses and names `cost`.

**Figure 3** — `docs/figures/fig3_score.png`. `make score`: per-question verdicts and the cross-arm comparison, regenerated from committed traces with no API key.

**Figure 4** — `docs/figures/fig4_reproduce.png`. `make verify-data`, `make test` (79 passing), and `make demo` reproducing the headline rows offline.
