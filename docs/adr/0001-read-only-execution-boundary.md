# ADR 0001: Enforce read-only execution with three independent controls

**Status:** accepted, implemented in `src/db.py`

## Context

The first baseline enforced "read-only" with a single regular expression over the
raw query text, on a read-write connection. Review found the control failed in
both directions:

* `CREATE TABLE pwned (x INT)` succeeded on the same connection object that
  executes model-generated SQL — the regex was the *only* thing preventing a
  write.
* `SELECT 'please update your address'` was rejected, because keyword matching on
  raw text cannot distinguish a statement from a string literal.
* `SELECT COUNT(*) FROM orders a, orders b, orders c` was accepted: 27M
  intermediate rows, no timeout, no row cap. `max_rows` was applied *after*
  `pd.read_sql_query` had already materialised the whole result.

## Decision

Three independent controls, so no single failure is sufficient:

1. **Connection permission** — open with `?mode=ro&uri=True`. SQLite refuses
   writes itself, regardless of what passes the layers above.
2. **Statement authorizer** — during execution, `set_authorizer` permits only
   `SQLITE_READ`, `SQLITE_SELECT`, `SQLITE_FUNCTION` and recursive-CTE actions.
   DDL, `ATTACH` and `PRAGMA` are denied by the engine, not by string matching.
   The authorizer is installed per query and removed afterwards, which is why
   schema introspection can still use `PRAGMA`.
3. **Cost budget** — a progress handler interrupts any query exceeding a VM-step
   or wall-clock budget, and rows are streamed with `fetchmany(max_rows + 1)` so
   truncation is detected and flagged rather than silently applied.

The lexical check is kept, but demoted: it exists to produce a clear error
message, and it now blanks string literals and comments first.

## Alternatives considered

* **`sqlglot` AST validation.** Stronger identifier resolution and the right tool
  for the `validate` node in Phase 2, but it adds a dependency and is still only
  a parser — it cannot stop a write that reaches the connection. Deferred to
  Phase 2 *in addition to*, not instead of, the permission boundary.
* **A restricted database role.** Correct for a warehouse, unavailable in SQLite;
  `?mode=ro` is the equivalent.

## Consequences

`tests/test_guard.py` pins all three probes as regressions. `REPLACE()` had to be
removed from the forbidden-keyword list because it is a legitimate scalar
function — evidence that the lexical layer should not be trusted alone.
