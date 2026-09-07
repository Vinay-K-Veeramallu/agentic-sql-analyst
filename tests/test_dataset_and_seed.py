"""Tests over the evidence itself: the eval set, the seeder, and the traces.

If these pass, every number quoted in PROPOSAL.md is regenerable from the
repository. That is the claim section 1 of the proposal makes, so it is tested
rather than asserted.
"""

import hashlib
import json
import os
import subprocess
import sys

import pytest

import dataset
import db

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED_SCRIPT = os.path.join(ROOT, "scripts", "seed_db.py")
RESULTS_DIR = os.path.join(ROOT, "eval", "results")


# --------------------------------------------------------------------------- #
# The evaluation set must be internally valid
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def questions():
    return dataset.load_questions()


def test_questions_load_and_ids_are_unique(questions):
    assert len(questions) >= 16
    ids = [q.id for q in questions]
    assert len(ids) == len(set(ids))


def test_every_reference_query_runs_and_returns_rows(questions, conn):
    """A reference query that does not run cannot decide anything."""
    for question in questions:
        if question.kind != "reference":
            continue
        columns, rows, _ = db.fetch_rows(conn, question.reference_sql, max_rows=None)
        assert columns, f"{question.id}: reference produced no columns"
        assert rows, f"{question.id}: reference produced no rows"


def test_reference_queries_are_read_only(questions, conn):
    for question in questions:
        if question.kind == "reference":
            db.guard_sql(question.reference_sql)


def test_unanswerable_questions_declare_accepted_fields(questions):
    unanswerable = [q for q in questions if q.kind == "unanswerable"]
    assert len(unanswerable) >= 6
    for question in unanswerable:
        assert question.accept_missing
        assert question.missing_field


def test_unanswerable_fields_are_genuinely_absent_from_the_schema(questions, conn):
    """Guard against an 'unanswerable' item that the schema can in fact answer."""
    schema, _ = db.get_schema_string(conn)
    lowered = schema.lower()
    for question in questions:
        if question.kind != "unanswerable":
            continue
        for term in question.accept_missing:
            assert term.lower() not in lowered, (
                f"{question.id}: '{term}' appears in the schema, so the question "
                f"may not be unanswerable"
            )


def test_ambiguous_questions_declare_their_undefined_terms(questions):
    for question in questions:
        if question.kind == "ambiguous":
            assert question.undefined_terms


# --------------------------------------------------------------------------- #
# The seeder must be deterministic - the ground truth depends on it
# --------------------------------------------------------------------------- #

def _md5(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.md5(handle.read()).hexdigest()


def test_seeder_is_byte_deterministic(tmp_path):
    first, second = tmp_path / "a.db", tmp_path / "b.db"
    for target in (first, second):
        subprocess.run([sys.executable, SEED_SCRIPT, "--db", str(target)],
                       check=True, capture_output=True)
    assert _md5(str(first)) == _md5(str(second))


def test_seeded_ground_truth_matches_the_proposal(tmp_path):
    """The three rows quoted in PROPOSAL.md section 4, recomputed."""
    target = tmp_path / "seeded.db"
    subprocess.run([sys.executable, SEED_SCRIPT, "--db", str(target)],
                   check=True, capture_output=True)
    conn = db.connect(str(target))
    _, rows, _ = db.fetch_rows(conn, """
        SELECT c.customer_name, ROUND(SUM(o.total_amount), 2)
        FROM orders o JOIN customers c ON c.customer_id = o.customer_id
        GROUP BY c.customer_name ORDER BY 2 DESC LIMIT 3
    """, max_rows=None)
    conn.close()
    assert rows == [("Alice Johnson", 10643.33),
                    ("Bob Smith", 10539.88),
                    ("Laura Wilson", 9250.98)]


# --------------------------------------------------------------------------- #
# Committed traces must stay loadable and self-consistent
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name", [
    "b0_live_20260906.jsonl",
    "b0_strata_20260906.jsonl",
    "b1_live_20260906.jsonl",
    "b1_strata_20260906.jsonl",
])
def test_committed_traces_are_valid_and_reference_known_questions(name, questions):
    path = os.path.join(RESULTS_DIR, name)
    known = {q.id for q in questions}
    with open(path, "r", encoding="utf-8") as handle:
        records = [json.loads(line) for line in handle if line.strip()]
    assert records
    for record in records:
        assert record["question_id"] in known
        assert record["arm"] in ("b0", "b1")
        assert record["prompt_version"] in ("b0-v1", "b1-v1")
        if record.get("refused"):
            assert record.get("missing_field")
        if record.get("exec_ok"):
            assert record.get("latency_ms") is not None


def test_b0_trace_excludes_exactly_one_cache_hit():
    path = os.path.join(RESULTS_DIR, "b0_live_20260906.jsonl")
    with open(path, "r", encoding="utf-8") as handle:
        records = [json.loads(line) for line in handle if line.strip()]
    excluded = [r for r in records if r.get("exclude_from_latency")]
    assert len(excluded) == 1
    assert excluded[0]["question_id"] == "E1"
    assert excluded[0]["latency_ms"] == 860.0
