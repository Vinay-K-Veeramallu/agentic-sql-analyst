# agentic-sql-analyst

**QueryMind** — a self-correcting agentic Text-to-SQL assistant for enterprise
data analytics. CSE 598 (Agentic AI) capstone — *Vinay Kumar Veeramallu*.

This branch contains the **runnable baseline**: a deliberately simple,
single-call Text-to-SQL script. It takes a natural language question, inspects a
SQLite schema, asks a model for one SQL query, executes it, and prints the
result.

There is **no retry loop, no reflection, and no UI**. That is intentional — the
baseline defines the floor that the agentic system will be measured against.

---

## 1. Requirements

- Python **3.10+** (developed and tested on 3.11)
- No database server needed — the project uses a local SQLite file
- An API key is **optional**: `--provider mock` runs fully offline

## 2. Setup

```bash
git clone https://github.com/Vinay-K-Veeramallu/agentic-sql-analyst.git
cd agentic-sql-analyst
git checkout baseline

python3.11 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 3. Environment variables

Only required for the live-model providers.

```bash
cp .env.example .env
```

Then edit `.env`:

| Variable            | Provider | Description                                                  |
| ------------------- | -------- | ------------------------------------------------------------ |
| `OPENCODE_BASE_URL` | `asu`    | `https://openai.rc.asu.edu/v1` (pre-filled in `.env.example`) |
| `OPENCODE_API_KEY`  | `asu`    | Key from the [Voyager portal](https://voyager.rc.asu.edu) → **LLM Access** → **Create Key** |
| `OPENAI_API_KEY`    | `openai` | Standard OpenAI key                                          |

**If you have no key, skip this step entirely and use `--provider mock`.**

## 4. Build the database

```bash
python scripts/seed_db.py
```

Creates `data/analytics.db` with a fixed random seed (`598`), so every user gets
byte-identical data:

| Table       | Rows | Columns                                                                           |
| ----------- | ---- | --------------------------------------------------------------------------------- |
| `customers` | 20   | `customer_id`, `customer_name`, `region`, `signup_date`                            |
| `products`  | 15   | `product_id`, `product_name`, `category`, `unit_price`                             |
| `orders`    | 300  | `order_id`, `customer_id`, `product_id`, `quantity`, `total_amount`, `order_date`  |

`orders` has foreign keys into both other tables, so most questions require a
multi-table JOIN.

## 5. Run the baseline

Offline, no API key (**start here**):

```bash
python src/baseline.py --input examples/test1.txt --provider mock
```

Against ASU's OpenCode gateway:

```bash
python src/baseline.py --input examples/test1.txt --provider asu
```

Ask an ad-hoc question:

```bash
python src/baseline.py --query "What is the total revenue by region?" --provider asu
```

Run the 5-question mini benchmark and print a success rate:

```bash
python src/baseline.py --input examples/queries.txt --provider asu
```

### CLI flags

| Flag         | Meaning                                                   |
| ------------ | --------------------------------------------------------- |
| `--query`    | A single natural language question                         |
| `--input`    | A file with one question per line                          |
| `--provider` | `asu` \| `openai` \| `mock`                                |
| `--model`    | Override the model name (default `muse-glimmer-30b`)       |
| `--db`       | Override the SQLite path                                   |
| `--config`   | Override the config file (default `config/settings.yaml`)  |

## 6. Where things live

| What                    | Path                    |
| ----------------------- | ----------------------- |
| Configuration           | `config/settings.yaml`  |
| Baseline entry point    | `src/baseline.py`       |
| Schema introspection    | `src/db.py`             |
| Prompting and providers | `src/llm.py`            |
| Database seeder         | `scripts/seed_db.py`    |
| Sample input            | `examples/test1.txt`    |
| Benchmark questions     | `examples/queries.txt`  |
| Hard / ambiguous set    | `examples/hard_queries.txt` |
| Raw live-run log        | `docs/baseline_run_output.txt` |
| Console output          | stdout                  |
| Persistent log          | `logs/baseline_run.log` |
| Proposal document       | `PROPOSAL.md`           |
| Run screenshot          | `docs/screenshot.png`   |

## 7. How the baseline works

```
[Natural language question]
          |
          v
[PRAGMA table_info  ->  schema string]
          |
          v
[System prompt + schema + question]
          |
          v
[ONE chat-completion call]  ->  raw SQL
          |
          v
[Read-only guard: SELECT / WITH only]
          |
          v
[SQLite execution]  ->  pandas DataFrame  ->  stdout + log
```

The guard rejects any statement that is not a single read-only `SELECT`/`WITH`.
`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `ATTACH`, `PRAGMA` and stacked
statements are all refused before touching the database.

**On failure the script logs the error and exits non-zero. It does not retry.**

## 8. About `--provider mock`

`mock` is a deterministic, offline, rule-based stub. It keyword-matches the
question against a small table of hand-written SQL queries. It exists so that a
grader without credentials can still verify the pipeline runs end to end.

It is **not** the system under study, and its accuracy is not reported as a
baseline result.

## 9. Measured baseline results

Against `muse-glimmer-30b` on the ASU RC gateway, 11 live runs:

| | Result |
| --- | --- |
| Execution success (no SQL error) | **11/11 (100%)** |
| Answer accuracy, `examples/queries.txt` | **5/5** |
| Answer accuracy, `examples/hard_queries.txt` | **2/5** |
| Latency p50 / mean / max | **16.45s / 27.0s / 121.8s** |

The headline finding is that the baseline makes **no SQL syntax errors at all**.
Its failures are semantic: it answered "what is the profit margin per category?"
with a table of zeros, even though this schema has no cost column and margin is
not computable. Raw log: `docs/baseline_run_output.txt`. Full analysis in
`PROPOSAL.md` §4.

## 10. Known limitations

- No self-correction: a single malformed query ends the run.
- No natural language summary of the result table, and no charts.
- Ambiguous questions ("show sales trends") get an arbitrary interpretation
  rather than a clarifying question.
- Only tested against local SQLite; no warehouse connectors.
- The read-only guard is keyword-based. That is adequate for this SQLite demo
  but is not a substitute for a genuinely restricted database role.

## 11. Next steps

Rebuild the control flow in **LangGraph** with an explicit self-correction node,
add result summarisation and Plotly charts as tools, and wrap it in a Streamlit
chat UI. See `PROPOSAL.md` §7.
