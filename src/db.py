"""SQLite helpers: connection, schema introspection, and read-only execution."""

import os
import re
import sqlite3
from typing import List, Tuple

import pandas as pd

FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|TRUNCATE|ATTACH|DETACH|PRAGMA|VACUUM)\b",
    re.IGNORECASE,
)


class UnsafeQueryError(Exception):
    """Raised when the model produces something other than a read-only SELECT."""


def connect(db_path: str) -> sqlite3.Connection:
    if not os.path.exists(db_path):
        raise FileNotFoundError(
            f"Database not found at {db_path}. Run: python scripts/seed_db.py"
        )
    return sqlite3.connect(db_path)


def list_tables(conn: sqlite3.Connection) -> List[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def get_schema_string(conn: sqlite3.Connection) -> Tuple[str, List[str]]:
    """Render the schema via PRAGMA introspection into a compact prompt block."""
    tables = list_tables(conn)
    blocks = []
    for table in tables:
        cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
        col_lines = [f"    {c[1]} {c[2]}" for c in cols]
        fks = conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
        fk_lines = [f"    -- {table}.{fk[3]} -> {fk[2]}.{fk[4]}" for fk in fks]
        blocks.append(
            f"TABLE {table} (\n" + "\n".join(col_lines + fk_lines) + "\n)"
        )
    return "\n\n".join(blocks), tables


def run_select(conn: sqlite3.Connection, sql: str, max_rows: int = 50) -> pd.DataFrame:
    """Execute `sql` after rejecting anything that is not a single read-only SELECT."""
    statement = sql.strip().rstrip(";").strip()

    if ";" in statement:
        raise UnsafeQueryError("Multiple SQL statements are not allowed.")
    if not re.match(r"^\s*(SELECT|WITH)\b", statement, re.IGNORECASE):
        raise UnsafeQueryError("Only SELECT / WITH queries are permitted.")
    if FORBIDDEN.search(statement):
        raise UnsafeQueryError("Query contains a write or DDL keyword.")

    return pd.read_sql_query(statement, conn).head(max_rows)
