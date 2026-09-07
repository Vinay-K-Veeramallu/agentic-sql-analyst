# Capstone Project Proposal

## Basic Information

| Field                   | Response                                                                            |
| ----------------------- | ----------------------------------------------------------------------------------- |
| Student name            | Vinay Kumar Veeramallu                                                              |
| Project title           | QueryMind: A Self-Correcting Agentic Text-to-SQL Assistant for Enterprise Analytics  |
| Repository / notebook    | https://github.com/Vinay-K-Veeramallu/agentic-sql-analyst (branch: `baseline`)      |
| Configuration location  | `config/settings.yaml` (env vars documented in `README.md` §3 and `.env.example`)   |

---

## Section 1. Problem Definition

**Task.** Build an autonomous Text-to-SQL data-analyst agent. Given a question in
plain English and a relational database, the system inspects the schema,
generates a SQL query, executes it, recovers from execution errors on its own,
and reports the answer.

**Intended user and situation.** Non-technical business staff — product
managers, sales leads, operations analysts — who need a number out of a company
database *now* and would otherwise file a ticket with a data engineer and wait
days. They know their business vocabulary; they do not know the schema and
cannot write SQL.

**Input.** A single natural-language string, e.g.
`"Who are the top 3 customers by total spending?"`, plus a connection to a
read-only SQLite analytics database.

**Output.** (a) the executed SQL, (b) the result rows, and — in the final
system — (c) a one-paragraph natural-language answer and (d) a chart.

**Success.** The system emits valid, read-only SQL that executes without error
(or recovers within its retry budget), and the returned rows match the
ground-truth result of a hand-written reference query, within 15 seconds. For a
question the schema cannot answer, success means an explicit refusal that names
the missing data — not a plausible-looking table.

**Failure.** Any of: the SQL does not parse; it references a table or column
that does not exist; execution raises and the system cannot recover; the query
runs but returns numbers inconsistent with the true database state; the system
answers an unanswerable question with fabricated output; or it attempts a write
operation.

Correctness is checked mechanically by comparing the returned result set against
a reference query, so success and failure are decidable by a third party rather
than a matter of opinion.

---

## Section 2. Motivation and Project Scope

**Why it matters.** The bottleneck in most analytics organisations is not
storage or compute but the queue of ad-hoc questions waiting on a small data
team. Removing even the simple questions from that queue is a real productivity
win, and Text-to-SQL is the narrowest useful slice of it.

**Why agentic AI is the right shape.** A single-shot LLM call emits one query
and has no way to check it. An agent can (i) call a schema-inspection tool
instead of guessing, (ii) validate every identifier in the proposed query
against the real schema *before* executing it, (iii) read the database's error
message and repair the query when execution fails, and (iv) — the point my
baseline measurements made unavoidable — recognise when the schema cannot answer
the question at all and refuse rather than fabricate. The environment supplies a
cheap, automatic signal at each of those steps, which is exactly the condition
under which an act-observe-revise loop pays for itself. That loop is the
research content of the project, not the LLM call.

The baseline measurements in §4 already sharpen this: step (iii) alone is nearly
worthless here, because the model produced **zero** SQL errors across 11 live
runs. The available value is concentrated in (ii) and (iv), so that is where the
agent design is aimed.

| In scope this semester                                   | Deliberately out of scope                                  |
| -------------------------------------------------------- | ---------------------------------------------------------- |
| Read-only querying of a local SQLite database             | `INSERT` / `UPDATE` / `DELETE` and any schema modification  |
| Schema-introspection tool + multi-table JOIN handling     | Cloud warehouses (Snowflake, BigQuery), auth, multi-tenancy |
| Self-correction loop, hard-capped at 3 retries            | Unbounded planning or open-ended agent autonomy             |
| Result summarisation + chart generation                   | RAG over unstructured PDFs or documents                     |
| Streamlit chat UI                                         | Production deployment, monitoring, cost controls            |
| A 30-question evaluation set with reference SQL           | Fine-tuning or training a model                             |

The scope is small on purpose: one database, one dialect, one loop. What makes
it meaningful is that the improvement over the baseline is *measurable* rather
than asserted.

---

## Section 3. Runnable Baseline

**Stack.** Python 3.11, the `openai` client library pointed at ASU's OpenCode
gateway (model `muse-glimmer-30b`), `sqlite3`, and `pandas`. No agent framework
is used — that is the point.

**Step by step** (`src/baseline.py`):

1. Connect to `data/analytics.db`.
2. Introspect every table with `PRAGMA table_info` and `PRAGMA
   foreign_key_list`, rendering the result into a compact schema string
   (`src/db.py`).
3. Compose one prompt: system instructions + schema + user question
   (`src/llm.py`).
4. Make **exactly one** chat-completion call and take the reply as SQL.
5. Strip markdown fences, then apply a read-only guard that rejects anything
   which is not a single `SELECT`/`WITH`.
6. Execute against SQLite, print the resulting DataFrame, and append a record to
   `logs/baseline_run.log`.

If step 4 or step 6 fails, the script logs the error and exits non-zero. **There
is no retry.**

**Why this is a reasonable starting point.** It isolates the one variable the
capstone is about. The baseline and the final system will share the same
database, the same prompt scaffold, the same model and the same evaluation
harness; the only difference will be the control loop. Any measured improvement
is therefore attributable to the agentic structure rather than to a better
prompt or a bigger model.

**Files.**

| Purpose                        | File                    |
| ------------------------------ | ----------------------- |
| Baseline entry point           | `src/baseline.py`       |
| Schema introspection + guard   | `src/db.py`             |
| Prompt template and providers  | `src/llm.py`            |
| Deterministic database seeder  | `scripts/seed_db.py`    |
| Configuration                  | `config/settings.yaml`  |

A third provider, `--provider mock`, is a deterministic offline rule-based stub
included **only** so that a grader without credentials can still execute the
pipeline. Its accuracy is never reported as a baseline result.

---

## Section 4. Test Case and Baseline Output

**Sample input** (`examples/test1.txt`):

```
Who are the top 3 customers by total spending?
```

**Database.** `data/analytics.db`, seeded with fixed seed `598`: `customers`
(20 rows), `products` (15 rows), `orders` (300 rows). Answering the question
requires joining `orders` to `customers` on a foreign key — the answer is not
available from any single table.

**Expected behaviour.** Produce a query equivalent to the reference:

```sql
SELECT c.customer_name, SUM(o.total_amount) AS total_spent
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id
GROUP BY c.customer_name
ORDER BY total_spent DESC
LIMIT 3;
```

**Ground truth** (computed directly against the seeded database):

| customer_name | total_spent |
| ------------- | ----------- |
| Alice Johnson | 10643.33    |
| Bob Smith     | 10539.88    |
| Laura Wilson  | 9250.98     |

**Actual baseline output.** Command:

```
python src/baseline.py --input examples/test1.txt --provider asu
```

```
[INFO] Provider=asu model=muse-glimmer-30b db=/.../agentic-sql-analyst/data/analytics.db
[INFO] Schema loaded successfully for tables: ['customers', 'orders', 'products']
[INFO] Question: Who are the top 3 customers by total spending?
[INFO] Generated SQL: SELECT c.customer_name, SUM(o.total_amount) AS total_spending
       FROM customers c JOIN orders o ON o.customer_id = c.customer_id
       GROUP BY c.customer_id, c.customer_name ORDER BY total_spending DESC LIMIT 3
[INFO] Execution succeeded in 13.41s, 3 row(s) returned

customer_name  total_spending
Alice Johnson        10643.33
    Bob Smith        10539.88
 Laura Wilson         9250.98
```

The returned rows match the ground-truth table above exactly.

Screenshot of the successful run: `docs/screenshot.png`. The unedited log of
every run described below is committed at `docs/baseline_run_output.txt`.

### Beyond the single test case

The baseline was also run over two further question sets against the live model,
which is where the interesting result is.

*Easy set* (`examples/queries.txt`, 5 questions — single-table aggregates and
two-table JOINs): **5/5 executed, 5/5 answers correct** when checked against
hand-written reference SQL.

*Hard set* (`examples/hard_queries.txt`, 5 deliberately ambiguous or multi-hop
questions): **5/5 executed without error, but only 2/5 are defensible answers.**

**What worked — better than expected.** The model never produced a syntax error
and never hallucinated a table or column name across 11 live runs. It joined on
the correct foreign keys unprompted, aliased aggregates sensibly, and reached
for window functions (`ROW_NUMBER() OVER (PARTITION BY ...)`) correctly on
"list the second-most-popular product in each category". The read-only guard
never had to fire.

**What did not work — and it reframes the project.** My original hypothesis was
that the baseline would fail on *SQL syntax*, and that a retry loop reading
SQLite's error messages would be the fix. The measurements do not support that.
The real failure mode is **silent semantic error: queries that execute
perfectly and return confidently wrong answers.**

1. *Unanswerable questions get fabricated answers.* Asked "what is the profit
   margin per category?", the baseline emitted
   `SUM(o.total_amount - o.quantity * p.unit_price) / SUM(o.total_amount)` and
   returned `0.0` for every category. There is no cost column in this schema, so
   margin is not computable; because `total_amount` is defined as
   `quantity * unit_price`, the expression is identically zero by construction.
   The correct behaviour was to say "this database does not contain cost data."
   Instead the user receives a clean table of zeros. **No retry loop would ever
   catch this, because nothing errored.**
2. *Ambiguity is silently resolved.* "Show sales trends over time" was answered
   at monthly granularity with two pointless JOINs; "customers who signed up
   early but never became high spenders" invented both thresholds — earliest 25%
   by signup date, below-average lifetime spend — without flagging either.
3. *Latency badly misses the 15-second target.* Across 11 live runs: p50 =
   16.45s, mean = 27.0s, max = **121.8s**, and **6 of 11 runs exceeded 15s**.
   The slowest was the longest generated query, which suggests latency scales
   with output tokens rather than with database work — SQLite itself returns in
   milliseconds.

This is the most useful thing the baseline told me, and it changes the plan:
the agent's job is **verification and honest refusal**, not just retrying on
error. Sections 6 and 7 are written against this evidence rather than against my
initial assumption.

---

## Section 5. Reproducibility and Run Instructions

Full detail is in `README.md`. Summary:

```bash
git clone https://github.com/Vinay-K-Veeramallu/agentic-sql-analyst.git
cd agentic-sql-analyst
git checkout baseline

python3.11 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

python scripts/seed_db.py         # builds data/analytics.db

# No API key required:
python src/baseline.py --input examples/test1.txt --provider mock

# With ASU OpenCode credentials in .env:
python src/baseline.py --input examples/test1.txt --provider asu
```

**Environment variables** (only for `--provider asu`; copy `.env.example` to
`.env`):

```
OPENCODE_BASE_URL=https://openai.rc.asu.edu/v1
OPENCODE_API_KEY=<your ASU RC key from https://voyager.rc.asu.edu>
```

- **Input:** `examples/test1.txt`; larger sets in `examples/queries.txt` and
  `examples/hard_queries.txt`.
- **Output:** printed to stdout; also appended to `logs/baseline_run.log`.
- **Runtime:** setup under two minutes; a single query is roughly one second
  plus model latency.

**Known setup limitations.** Requires Python 3.10+. The `asu` provider needs an
ASU RC LLM API key from the Voyager portal, which a grader outside ASU cannot
obtain — which is precisely why the offline `mock` provider exists.
Reproducibility of the pipeline does not depend on holding ASU credentials. Only
SQLite is supported.

---

## Section 6. Initial Evaluation Plan

Both systems will be run over the same 30-question set, each paired with a
hand-written reference SQL query. The set will be stratified: 10 easy
(single-table / two-table aggregates), 15 hard (multi-hop JOINs, window
functions, relative time expressions), and **5 unanswerable** — questions whose
answer is not derivable from the schema. On the unanswerable items the only
correct behaviour is an explicit refusal; any table of numbers is a failure.

A question counts as correct when the returned result set matches the reference
result set, order-insensitive for unordered queries, with float tolerance.

| Metric                        | How it is measured                            | Baseline (measured, 11 live runs)     | Target for agentic system |
| ----------------------------- | --------------------------------------------- | ------------------------------------- | ------------------------- |
| Execution success rate        | queries that run without error                | **100% (11/11)**                      | maintain ≥ 98%            |
| Answer accuracy — easy set    | result set matches reference                  | **100% (5/5)**                        | maintain 100%             |
| Answer accuracy — hard set    | result set matches reference                  | **40% (2/5)**                         | ≥ 80%                     |
| Unanswerable-question refusal | refuses instead of fabricating                | **0%**                                | ≥ 90%                     |
| Ambiguity flagged             | states assumptions or asks for clarification  | **0%**                                | ≥ 75%                     |
| Latency p50 / max             | wall clock per question                       | **16.45s / 121.8s; 6 of 11 over 15s** | p50 < 15s, max < 45s      |
| Output completeness           | SQL only vs. SQL + summary + chart            | SQL only                              | all three                 |
| Answer quality                | LLM-as-a-judge, 1–5, on a held-out sample     | to be scored                          | ≥ 4.5                     |

Note what the baseline measurement already rules out. **Execution success rate
is a dead metric here — the baseline scores 100%**, so a self-correction loop
that only catches SQL errors has almost no headroom to demonstrate. Reporting an
improvement on that axis would be reporting noise.

The decisive evidence is therefore the pair **unanswerable-question refusal rate
and hard-set accuracy**. If the agent learns to say "this database has no cost
column, so profit margin cannot be computed" while its accuracy on the easy set
does not regress, the added machinery is doing real work. Latency is the cost
side of the ledger and is reported honestly: the baseline already violates its
own 15-second target on complex questions, so extra reasoning steps must be paid
for with caching or a smaller model, not ignored.

---

## Section 7. Limitations and Next Steps

**Known weaknesses of the baseline**, in the order the measurements justify:

- **Fabrication on unanswerable questions.** The profit-margin case (§4)
  produced a plausible table of zeros for a quantity the schema cannot express.
  This is the most damaging failure because it is invisible to the user.
- **Silent disambiguation.** Vague questions get one arbitrary interpretation
  with no statement of the assumptions made.
- **Latency.** p50 16.45s against a 15s target, with a 121.8s worst case.
- **No verification step.** Nothing checks the query against the question; the
  only check is that SQLite accepted it.
- Output is a raw table; the target user wanted an answer, not rows.
- The read-only guard is keyword-based. Adequate for this demo, but the real
  control should be a restricted database role.
- Command-line only.
- *Not* a weakness, contrary to my initial expectation: SQL syntax and schema
  grounding. Zero errors in 11 runs.

**Expected failure cases going forward.** Questions needing columns that do not
exist; relative time expressions ("Q3", "last quarter") with no anchor date;
metrics with contested definitions ("active customer"); questions where a JOIN
silently drops rows (customers with no orders); and larger schemas where the
whole schema no longer fits comfortably in the prompt.

**Next phases.**

- *Weeks 3–4:* rebuild the control flow in LangGraph as an explicit state
  machine — plan → inspect schema → generate → **validate** → execute → **verify**
  → answer — with a hard retry cap of 3 and full trace logging. The two added
  nodes are the ones the baseline evidence calls for: `validate` checks every
  identifier against the introspected schema and can return "not answerable from
  this schema"; `verify` re-reads the result table against the original question
  before it is shown.
- *Weeks 5–6:* add summarisation and Plotly charts as tools; build the
  30-question harness with reference SQL, including the 5 unanswerable items;
  add response caching to keep evaluation runs affordable given the latency
  measured above.
- *Weeks 7–8:* Streamlit chat UI, full baseline-vs-agent comparison, write-up.

**Risks and mitigations.** *Latency compounding* — the agent adds calls to a
pipeline whose p50 is already 16s; mitigate by running validation as a cheap
local schema check rather than a model call, and by caching. *Over-refusal* —
an agent tuned to refuse may start refusing answerable questions, so the easy
set is retained as a regression guard. *Retry loops that never converge* — hard
cap of 3 with an explicit "I could not answer this" fallback. *Evaluation set
too easy to separate the systems* — `examples/hard_queries.txt` already
demonstrates that the easy set saturates at 100%, so the 30-question set is
weighted toward hard and unanswerable items. *Gateway rate limits or outages* —
cache responses; the offline `mock` provider keeps the pipeline testable.

**What I may need.** Stable quota on the ASU RC gateway (`muse-glimmer-30b` via
`https://openai.rc.asu.edu/v1`), and roughly a day to hand-write reference SQL
for all 30 evaluation questions.
