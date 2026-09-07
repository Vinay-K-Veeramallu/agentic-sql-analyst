"""The scorer decides every accuracy figure in the proposal, so it is tested."""

import pytest

import dataset
import score


# --------------------------------------------------------------------------- #
# Result-set comparison
# --------------------------------------------------------------------------- #

def test_identical_sets_match():
    ok, _ = score.result_sets_match([("a", 1.0)], [("a", 1.0)], ordered=False)
    assert ok


def test_column_names_are_irrelevant_only_values_matter():
    """total_spent vs total_spending must not be scored as an error."""
    ok, _ = score.result_sets_match([("Alice", 10643.33)], [("Alice", 10643.33)],
                                    ordered=False)
    assert ok


def test_extra_candidate_columns_are_tolerated():
    """A broader answer that contains the answer still answers the question."""
    reference = [("West", 100.0, 200.0)]
    candidate = [("West", 100.0, 200.0, 7, 9)]
    ok, why = score.result_sets_match(reference, candidate, ordered=False)
    assert ok, why


def test_missing_information_is_not_tolerated():
    reference = [("West", 100.0, 200.0)]
    candidate = [("West", 100.0)]
    ok, _ = score.result_sets_match(reference, candidate, ordered=False)
    assert not ok


def test_row_order_ignored_when_unordered():
    reference = [("a", 1.0), ("b", 2.0)]
    candidate = [("b", 2.0), ("a", 1.0)]
    assert score.result_sets_match(reference, candidate, ordered=False)[0]


def test_row_order_enforced_when_ordered():
    reference = [("a", 1.0), ("b", 2.0)]
    candidate = [("b", 2.0), ("a", 1.0)]
    assert not score.result_sets_match(reference, candidate, ordered=True)[0]


def test_float_tolerance_at_six_decimals():
    assert score.result_sets_match([(1.0000001,)], [(1.0000002,)], ordered=False)[0]
    assert not score.result_sets_match([(1.0,)], [(1.01,)], ordered=False)[0]


def test_row_count_mismatch_fails():
    assert not score.result_sets_match([("a",)], [("a",), ("b",)], ordered=False)[0]


def test_nulls_compare_equal_and_differ_from_empty_string():
    assert score.result_sets_match([(None,)], [(None,)], ordered=False)[0]
    assert not score.result_sets_match([(None,)], [("",)], ordered=False)[0]


def test_wilson_interval_brackets_the_point_estimate():
    low, high = score.wilson(2, 5)
    assert low < 40.0 < high
    assert (round(low), round(high)) == (12, 77)


# --------------------------------------------------------------------------- #
# Per-kind decision rules
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def questions():
    return dataset.index_by_id(dataset.load_questions())


def test_fabrication_on_unanswerable_is_incorrect(questions, conn):
    record = {"exec_ok": True, "refused": False, "sql": "SELECT 1"}
    correct, verdict = score.score_record(questions["H3"], record, conn)
    assert not correct
    assert "FABRICATED" in verdict


def test_refusal_naming_the_right_field_is_correct(questions, conn):
    record = {"exec_ok": False, "refused": True, "missing_field": "unit_cost"}
    correct, _ = score.score_record(questions["H3"], record, conn)
    assert correct


def test_refusal_naming_the_wrong_field_is_incorrect(questions, conn):
    record = {"exec_ok": False, "refused": True, "missing_field": "customer_name"}
    correct, _ = score.score_record(questions["H3"], record, conn)
    assert not correct


def test_over_refusal_on_an_answerable_question_is_incorrect(questions, conn):
    record = {"exec_ok": False, "refused": True, "missing_field": "whatever"}
    correct, verdict = score.score_record(questions["E1"], record, conn)
    assert not correct
    assert "over-refusal" in verdict


def test_ambiguous_needs_stated_assumptions(questions, conn):
    silent = {"exec_ok": True, "refused": False, "sql": "SELECT 1"}
    stated = {"exec_ok": True, "refused": False, "sql": "SELECT 1",
              "assumptions": "monthly granularity, calendar year"}
    assert not score.score_record(questions["H1"], silent, conn)[0]
    assert score.score_record(questions["H1"], stated, conn)[0]


def test_reference_question_scored_against_the_live_database(questions, conn):
    """The committed reference SQL must reproduce itself."""
    record = {"exec_ok": True, "refused": False,
              "sql": questions["E1"].reference_sql}
    correct, why = score.score_record(questions["E1"], record, conn)
    assert correct, why


def test_wrong_answer_is_caught(questions, conn):
    record = {"exec_ok": True, "refused": False,
              "sql": "SELECT customer_name, 0.0 FROM customers LIMIT 3"}
    assert not score.score_record(questions["E1"], record, conn)[0]
