"""Prompt arms, reply parsing, and the frozen-baseline guarantee."""

import hashlib

import pytest

import llm

# SHA-256 of each measured arm's system prompt. These are the exact texts that
# produced the traces committed under eval/results/, so any edit must be a
# deliberate, versioned change rather than a silent one.
FROZEN_PROMPT_HASHES = {
    "b0": "077e7222b5962596c1d3148e82030ce079d1e1e99bcebfe74039ebd3e5133c1d",
    "b1": "30479e8f6609d1dbe8a265552e6a165641e7bce9baae0e434eb35dfb45649010",
}


# --------------------------------------------------------------------------- #
# Measured prompts are frozen byte for byte
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("arm,expected", sorted(FROZEN_PROMPT_HASHES.items()))
def test_prompt_arms_are_byte_frozen(arm, expected):
    """Any change to a measured prompt - not just a refusal clause - fails here."""
    actual = hashlib.sha256(llm.system_prompt(arm).encode("utf-8")).hexdigest()
    assert actual == expected, (
        f"\nThe '{arm}' system prompt changed.\n"
        f"  expected sha256: {expected}\n"
        f"  actual   sha256: {actual}\n\n"
        f"The traces in eval/results/{arm}_*.jsonl were produced with the previous\n"
        f"text, so editing this arm in place silently invalidates every number the\n"
        f"proposal reports for it. To change it deliberately, either:\n"
        f"  (a) add a NEW arm (e.g. '{arm}v2') in src/llm.py and leave '{arm}' alone, or\n"
        f"  (b) bump ARMS['{arm}']['version'], update the hash in this test, and re-run\n"
        f"      that arm's measurements, replacing eval/results/{arm}_*.jsonl.\n"
    )


def test_every_measured_arm_is_covered_by_a_frozen_hash():
    """A new arm cannot be added to ARMS without also being pinned."""
    assert set(llm.ARMS) == set(FROZEN_PROMPT_HASHES), (
        "Arms in src/llm.py and pinned hashes have diverged: "
        f"{sorted(set(llm.ARMS) ^ set(FROZEN_PROMPT_HASHES))}"
    )


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
