"""Regression tests for the read-only boundary.

Each test named `probe_N` reproduces a defect found in review of the first
baseline, where the only control was a regex over the raw query text.
"""

import sqlite3

import pytest

import db


# --------------------------------------------------------------------------- #
# probe 1: the connection itself must refuse writes
# --------------------------------------------------------------------------- #

def test_probe1_connection_is_read_only(conn):
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute("CREATE TABLE pwned (x INT)")


def test_probe1_insert_refused_by_connection(conn):
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute("INSERT INTO customers VALUES (999, 'x', 'y', '2024-01-01')")


# --------------------------------------------------------------------------- #
# probe 2: legitimate read-only SQL must not be rejected
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("sql", [
    "SELECT 'please update your address' AS notice",
    "SELECT customer_name FROM customers WHERE region = 'a;b'",
    "SELECT REPLACE(customer_name, 'a', 'A') AS n FROM customers LIMIT 2",
    "SELECT 'drop table orders' AS instruction",
    "-- a leading comment\nSELECT 1 AS one",
    "WITH x AS (SELECT 1 AS a) SELECT a FROM x",
])
def test_probe2_legitimate_queries_are_allowed(conn, sql):
    columns, rows, truncated = db.fetch_rows(conn, sql, max_rows=5)
    assert isinstance(rows, list)
    assert truncated is False


# --------------------------------------------------------------------------- #
# probe 3: query cost must be bounded
# --------------------------------------------------------------------------- #

def test_probe3_runaway_join_is_interrupted(conn):
    with pytest.raises(db.QueryBudgetError):
        db.fetch_rows(
            conn, "SELECT COUNT(*) FROM orders a, orders b, orders c",
            max_rows=1, max_steps=100_000, timeout_s=3.0,
        )


def test_row_cap_streams_and_flags_truncation(conn):
    frame = db.run_select(conn, "SELECT * FROM orders", max_rows=5)
    assert len(frame) == 5
    assert frame.attrs["truncated"] is True


def test_row_cap_not_flagged_when_complete(conn):
    frame = db.run_select(conn, "SELECT * FROM customers", max_rows=50)
    assert len(frame) == 20
    assert frame.attrs["truncated"] is False


# --------------------------------------------------------------------------- #
# Statements that must be refused
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("sql", [
    "DROP TABLE orders",
    "DELETE FROM orders",
    "UPDATE customers SET region = 'X'",
    "INSERT INTO customers VALUES (21, 'x', 'y', 'z')",
    "SELECT 1; DROP TABLE orders",
    "PRAGMA table_info(orders)",
    "ATTACH DATABASE '/tmp/evil.db' AS evil",
    "CREATE TABLE t (x INT)",
    "",
    "   ",
])
def test_unsafe_statements_are_refused(conn, sql):
    with pytest.raises(db.UnsafeQueryError):
        db.fetch_rows(conn, sql, max_rows=5)


def test_authorizer_is_removed_after_execution(conn):
    """The guard must not leave PRAGMA introspection broken for the next call."""
    db.fetch_rows(conn, "SELECT 1", max_rows=1)
    assert conn.execute("PRAGMA table_info(orders)").fetchall()


def test_schema_string_lists_tables_and_foreign_keys(conn):
    schema, tables = db.get_schema_string(conn)
    assert tables == ["customers", "orders", "products"]
    assert "TABLE orders (" in schema
    assert "-- orders.customer_id -> customers.customer_id" in schema


def test_strip_literals_blanks_only_literals():
    code = db.strip_literals_and_comments(
        "SELECT 'drop table x' /* DELETE */ -- UPDATE\n FROM t"
    )
    assert "drop table x" not in code
    assert "DELETE" not in code
    assert "UPDATE" not in code
    assert "FROM t" in code
