"""SQLite access layer: introspection plus a genuinely read-only execution path.

Three *independent* controls stand between model-generated SQL and the database,
so that no single failure is sufficient:

1. Connection permission - the file is opened with ``?mode=ro``, so any write is
   refused by SQLite itself even if every check above it is bypassed.
2. Statement authorizer  - while a generated query runs, only ``SQLITE_READ``,
   ``SQLITE_SELECT``, ``SQLITE_FUNCTION`` and recursive-CTE actions are
   authorised. DDL, ``ATTACH`` and ``PRAGMA`` are denied by the engine rather
   than by string matching.
3. Cost budget           - a progress handler interrupts any query exceeding a
   fixed VM-step or wall-clock budget, and rows are streamed with a hard cap
   instead of the whole result set being materialised in memory.

A lexical pre-check runs in front of these, but it is deliberately *not* the
security boundary - it exists only to return a clear error message. It strips
string literals and comments before looking for keywords, so a legitimate query
such as ``SELECT 'please update your address'`` is accepted, and it does not
need to be complete in order to be safe.
"""

import os
import re
import sqlite3
import time
from contextlib import contextmanager
from typing import Iterable, List, Optional, Sequence, Tuple

import pandas as pd

DEFAULT_MAX_ROWS = 50
DEFAULT_MAX_STEPS = 5_000_000
DEFAULT_TIMEOUT_S = 10.0
PROGRESS_INTERVAL = 1_000

# Only used for error messages; the authorizer is the real boundary.
FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|ATTACH|DETACH|PRAGMA|VACUUM|REINDEX)\b",
    re.IGNORECASE,
)

_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"")
_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)

_ALLOWED_ACTIONS = {
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_FUNCTION,
}
for _optional in ("SQLITE_RECURSIVE",):  # present on newer Pythons
    if hasattr(sqlite3, _optional):
        _ALLOWED_ACTIONS.add(getattr(sqlite3, _optional))


class UnsafeQueryError(Exception):
    """Raised when a query is not a single read-only SELECT/WITH statement."""


class QueryBudgetError(Exception):
    """Raised when a query exceeds its VM-step or wall-clock budget."""


# --------------------------------------------------------------------------- #
# Connection and introspection
# --------------------------------------------------------------------------- #

def connect(db_path: str) -> sqlite3.Connection:
    """Open `db_path` read-only. Writes are refused by SQLite, not by a regex."""
    if not os.path.exists(db_path):
        raise FileNotFoundError(
            f"Database not found at {db_path}. Run: python scripts/seed_db.py"
        )
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def list_tables(conn: sqlite3.Connection) -> List[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def get_schema_string(conn: sqlite3.Connection) -> Tuple[str, List[str]]:
    """Render the schema via PRAGMA introspection into a compact prompt block.

    Introspection runs before the authorizer is installed, which is why PRAGMA
    is available here but denied to generated SQL.
    """
    tables = list_tables(conn)
    blocks = []
    for table in tables:
        cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
        col_lines = [f"    {c[1]} {c[2]}" for c in cols]
        fks = conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
        fk_lines = [f"    -- {table}.{fk[3]} -> {fk[2]}.{fk[4]}" for fk in fks]
        body = "\n".join(col_lines + fk_lines)
        blocks.append(f"TABLE {table} (\n{body}\n)")
    return "\n\n".join(blocks), tables


# --------------------------------------------------------------------------- #
# Guard (error messages) - see module docstring for the actual boundary
# --------------------------------------------------------------------------- #

def strip_literals_and_comments(sql: str) -> str:
    """Blank out string literals and comments so keyword checks see only code."""
    text = _BLOCK_COMMENT.sub(" ", sql)
    text = _LINE_COMMENT.sub(" ", text)
    return _STRING_LITERAL.sub("''", text)


def guard_sql(sql: str) -> str:
    """Return the normalised statement, or raise :class:`UnsafeQueryError`."""
    statement = (sql or "").strip().rstrip(";").strip()
    if not statement:
        raise UnsafeQueryError("Empty query.")

    code = strip_literals_and_comments(statement)
    if ";" in code:
        raise UnsafeQueryError("Multiple SQL statements are not allowed.")
    if not re.match(r"^\s*(SELECT|WITH)\b", code.strip(), re.IGNORECASE):
        raise UnsafeQueryError("Only SELECT / WITH queries are permitted.")
    if FORBIDDEN.search(code):
        raise UnsafeQueryError("Query contains a write or DDL keyword.")
    return statement


def _read_only_authorizer(action, arg1, arg2, db_name, trigger_name):  # noqa: ANN001
    return sqlite3.SQLITE_OK if action in _ALLOWED_ACTIONS else sqlite3.SQLITE_DENY


@contextmanager
def read_only_execution(conn: sqlite3.Connection,
                        max_steps: int = DEFAULT_MAX_STEPS,
                        timeout_s: float = DEFAULT_TIMEOUT_S):
    """Install the authorizer and cost budget for the duration of one query."""
    started = time.monotonic()
    steps = {"n": 0}

    def progress() -> int:
        steps["n"] += PROGRESS_INTERVAL
        over_steps = steps["n"] > max_steps
        over_time = (time.monotonic() - started) > timeout_s
        return 1 if (over_steps or over_time) else 0

    conn.set_authorizer(_read_only_authorizer)
    conn.set_progress_handler(progress, PROGRESS_INTERVAL)
    try:
        yield
    finally:
        conn.set_authorizer(None)
        conn.set_progress_handler(None, 0)


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #

def fetch_rows(conn: sqlite3.Connection, sql: str,
               max_rows: Optional[int] = DEFAULT_MAX_ROWS,
               max_steps: int = DEFAULT_MAX_STEPS,
               timeout_s: float = DEFAULT_TIMEOUT_S,
               ) -> Tuple[List[str], List[tuple], bool]:
    """Execute a guarded read-only query.

    Returns ``(columns, rows, truncated)``. ``max_rows=None`` fetches everything,
    which is what the scorer needs when comparing against a reference query.
    """
    statement = guard_sql(sql)
    with read_only_execution(conn, max_steps=max_steps, timeout_s=timeout_s):
        try:
            cursor = conn.execute(statement)
            if max_rows is None:
                rows = cursor.fetchall()
            else:
                rows = cursor.fetchmany(max_rows + 1)
            columns = [d[0] for d in cursor.description] if cursor.description else []
        except sqlite3.OperationalError as exc:
            if "interrupt" in str(exc).lower():
                raise QueryBudgetError(
                    f"Query exceeded its budget ({max_steps} steps / {timeout_s}s)."
                ) from exc
            raise
        except sqlite3.DatabaseError as exc:
            if "authoriz" in str(exc).lower():
                raise UnsafeQueryError(
                    "Statement denied by the read-only authorizer."
                ) from exc
            raise

    truncated = max_rows is not None and len(rows) > max_rows
    if truncated:
        rows = rows[:max_rows]
    return columns, rows, truncated


def run_select(conn: sqlite3.Connection, sql: str,
               max_rows: int = DEFAULT_MAX_ROWS,
               max_steps: int = DEFAULT_MAX_STEPS,
               timeout_s: float = DEFAULT_TIMEOUT_S) -> pd.DataFrame:
    """Execute a guarded read-only query and return it as a DataFrame.

    ``frame.attrs["truncated"]`` records whether the row cap was hit, so a
    truncated table is never silently mistaken for a complete answer.
    """
    columns, rows, truncated = fetch_rows(
        conn, sql, max_rows=max_rows, max_steps=max_steps, timeout_s=timeout_s
    )
    frame = pd.DataFrame(rows, columns=columns or None)
    frame.attrs["truncated"] = truncated
    frame.attrs["max_rows"] = max_rows
    return frame


def normalise_rows(rows: Iterable[Sequence]) -> List[tuple]:
    """Rows as plain tuples, for multiset comparison in the scorer."""
    return [tuple(r) for r in rows]
