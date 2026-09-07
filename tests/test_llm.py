"""Prompt arms, reply parsing, and the frozen-baseline guarantee."""

import pytest

import llm


# --------------------------------------------------------------------------- #
# The b0 prompt is the measurement floor and must stay frozen
# --------------------------------------------------------------------------- #

def test_b0_never_grants_permission_to_refuse():
    """If this fails, the committed b0 results no longer describe the code."""
    assert "NOT_ANSWERABLE" not in llm.system_prompt("b0")
    assert llm.ARMS["b0"]["version"] == "b0-v1"


def test_b1_is_b0_plus_exactly_the_refusal_clause():
    b0, b1 = llm.system_prompt("b0"), llm.system_prompt("b1")
    assert b1.startswith(b0)
    assert "NOT_ANSWERABLE" in b1[len(b0):]


def test_unknown_arm_is_rejected():
    with pytest.raises(ValueError):
        llm.system_prompt("b2")


def test_messages_carry_schema_and_question():
    messages = llm.build_messages("TABLE t (a INT)", "how many?", arm="b1")
    assert messages[0]["role"] == "system"
    assert "TABLE t (a INT)" in messages[1]["content"]
    assert "how many?" in messages[1]["content"]


# --------------------------------------------------------------------------- #
# Reply parsing
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("raw,expected", [
    ("SELECT 1", "SELECT 1"),
    ("```sql\nSELECT 1\n```", "SELECT 1"),
    ("```\nSELECT 1;\n```", "SELECT 1"),
    ("sql: SELECT 1", "SELECT 1"),
    ("Here is the query:\nSELECT 1;", "SELECT 1"),
    ("WITH x AS (SELECT 1) SELECT * FROM x", "WITH x AS (SELECT 1) SELECT * FROM x"),
])
def test_strip_sql_fences(raw, expected):
    assert llm.strip_sql_fences(raw) == expected


@pytest.mark.parametrize("raw,field", [
    ("NOT_ANSWERABLE: unit_cost", "unit_cost"),
    ("NOT_ANSWERABLE: product cost.", "product cost"),
    ("not_answerable: cost", "cost"),
    ("NOT_ANSWERABLE:cost", "cost"),
])
def test_parse_refusal_detects_and_names(raw, field):
    refused, named = llm.parse_refusal(raw)
    assert refused is True
    assert named == field


@pytest.mark.parametrize("raw", [
    "SELECT 1",
    "",
    "SELECT 'not_answerable' AS x",          # a literal is not a refusal token
    "SELECT c FROM t WHERE note = 'NOT_ANSWERABLE: cost'",
])
def test_parse_refusal_ignores_ordinary_sql(raw):
    refused, named = llm.parse_refusal(raw)
    assert refused is False
    assert named is None


def test_parse_refusal_accepts_the_token_on_its_own_line():
    refused, named = llm.parse_refusal("```\nNOT_ANSWERABLE: unit_cost\n```")
    assert refused is True
    assert named == "unit_cost"


# --------------------------------------------------------------------------- #
# Offline stub
# --------------------------------------------------------------------------- #

def test_mock_is_deterministic():
    first = llm.mock_generate("Who are the top 3 customers by total spending?")
    second = llm.mock_generate("Who are the top 3 customers by total spending?")
    assert first == second


def test_mock_refuses_only_when_the_arm_allows_it():
    question = "What is the profit margin per category?"
    assert "NOT_ANSWERABLE" not in llm.mock_generate(question, arm="b0")
    assert "NOT_ANSWERABLE" in llm.mock_generate(question, arm="b1")


def test_generate_via_mock_populates_the_trace_fields():
    result = llm.generate("mock", "TABLE t (a INT)",
                          "What is the profit margin per category?", arm="b1")
    assert result.refused is True
    assert result.missing_field == "unit_cost"
    assert result.sql == ""
    assert result.arm == "b1"
    assert result.prompt_version == "b1-v1"
