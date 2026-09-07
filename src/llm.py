"""Text-to-SQL generation: prompt arms and providers.

Arms
----
Every arm shares one schema block, one model and one execution path, so a
difference between arms is attributable to the single thing that changed.

* ``b0`` - the measurement floor. One call, no permission to refuse. This prompt
  is **frozen**: the live results committed under ``eval/results/`` were produced
  with it, so improving it would silently invalidate the baseline.
* ``b1`` - ``b0`` plus one sentence granting permission to refuse. This is the
  control arm for the project's central claim: if a single sentence recovers most
  of the refusal gain, that gain is not attributable to agentic architecture.

Providers
---------
* ``asu``    - ASU OpenCode gateway (OpenAI-compatible chat completions).
* ``openai`` - any OpenAI-compatible endpoint, so a grader can use their own key.
* ``mock``   - deterministic offline stub, no credentials required.

The ``mock`` rules were written from ``examples/queries.txt``, so its accuracy is
meaningless by construction and is never reported as a result. It exists so that
a grader without credentials can still execute the pipeline end to end.
"""

import os
import re
from dataclasses import dataclass, field
from typing import Optional, Tuple

TEMPERATURE = 0.0

_BASE_RULES = """You are a senior data analyst who writes SQLite SQL.

Rules:
1. Answer with a single SQL query and nothing else. No prose, no explanation.
2. Use only the tables and columns given in the schema. Never invent names.
3. The query must be read-only: SELECT or WITH only.
4. Do not terminate the query with a semicolon.
5. Prefer explicit JOINs on the declared foreign keys.
6. Give aggregated columns a readable alias.
"""

_REFUSAL_CLAUSE = """7. If the schema does not contain the data needed to answer the question, do
   not write a query. Reply with exactly one line:
   NOT_ANSWERABLE: <the missing field or table>
"""

ARMS = {
    "b0": {
        "version": "b0-v1",
        "system": _BASE_RULES,
        "description": "single call, no permission to refuse (frozen baseline)",
    },
    "b1": {
        "version": "b1-v1",
        "system": _BASE_RULES + _REFUSAL_CLAUSE,
        "description": "b0 + one sentence granting permission to refuse",
    },
}
DEFAULT_ARM = "b0"

USER_TEMPLATE = """Database schema:

{schema}

Question: {question}

SQL:"""

REFUSAL_PREFIX = "NOT_ANSWERABLE"
_REFUSAL_RE = re.compile(r"NOT_ANSWERABLE\s*:?\s*(.*)", re.IGNORECASE)


@dataclass
class Generation:
    """One model call: what came back, and what it cost."""
    sql: str = ""
    raw: str = ""
    arm: str = DEFAULT_ARM
    prompt_version: str = ARMS[DEFAULT_ARM]["version"]
    model: str = ""
    provider: str = ""
    temperature: float = TEMPERATURE
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    refused: bool = False
    missing_field: Optional[str] = None
    extra: dict = field(default_factory=dict)


def system_prompt(arm: str = DEFAULT_ARM) -> str:
    if arm not in ARMS:
        raise ValueError(f"Unknown arm: {arm}. Choose from {sorted(ARMS)}.")
    return ARMS[arm]["system"]


def build_messages(schema: str, question: str, arm: str = DEFAULT_ARM):
    return [
        {"role": "system", "content": system_prompt(arm)},
        {"role": "user",
         "content": USER_TEMPLATE.format(schema=schema, question=question)},
    ]


def parse_refusal(text: str) -> Tuple[bool, Optional[str]]:
    """Detect the machine-checkable refusal token and the field it names.

    The token must begin a line (ignoring markdown fences), so a string literal
    such as ``SELECT 'not_answerable' AS x`` is not mistaken for a refusal.
    """
    for line in (text or "").splitlines():
        candidate = line.strip().strip("`").strip()
        match = _REFUSAL_RE.match(candidate)
        if match:
            field_name = match.group(1).strip().rstrip(".").strip() or None
            return True, field_name
    return False, None


def strip_sql_fences(text: str) -> str:
    """Remove markdown fences and stray prose that models like to add."""
    text = (text or "").strip()
    fence = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1)
    text = re.sub(r"^\s*(sql|SQL)\s*:\s*", "", text.strip())

    match = re.search(r"\b(SELECT|WITH)\b", text, re.IGNORECASE)
    if match:
        text = text[match.start():]
    return text.strip().rstrip(";").strip()


# --------------------------------------------------------------------------- #
# Offline rule-based stub (accuracy meaningless by construction)
# --------------------------------------------------------------------------- #

MOCK_RULES = [
    (
        ("top", "customer"),
        """SELECT c.customer_name, SUM(o.total_amount) AS total_spent
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id
GROUP BY c.customer_name
ORDER BY total_spent DESC
LIMIT 3""",
    ),
    (
        ("categor", "revenue"),
        """SELECT p.category, SUM(o.total_amount) AS revenue
FROM orders o
JOIN products p ON p.product_id = o.product_id
GROUP BY p.category
ORDER BY revenue DESC""",
    ),
    (
        ("region",),
        """SELECT c.region, SUM(o.total_amount) AS revenue
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id
GROUP BY c.region
ORDER BY revenue DESC""",
    ),
    (
        ("month",),
        """SELECT strftime('%Y-%m', o.order_date) AS month, SUM(o.total_amount) AS revenue
FROM orders o
GROUP BY month
ORDER BY month""",
    ),
    (
        ("average", "order"),
        """SELECT AVG(total_amount) AS average_order_value FROM orders""",
    ),
]

# Only consulted for arm b1, to exercise the refusal path offline.
MOCK_REFUSALS = [(("profit",), "unit_cost"), (("margin",), "unit_cost")]

MOCK_FALLBACK = "SELECT COUNT(*) AS total_orders FROM orders"


def mock_generate(question: str, arm: str = DEFAULT_ARM) -> str:
    lowered = (question or "").lower()
    if arm != "b0":
        for keywords, missing in MOCK_REFUSALS:
            if all(k in lowered for k in keywords):
                return f"{REFUSAL_PREFIX}: {missing}"
    for keywords, sql in MOCK_RULES:
        if all(k in lowered for k in keywords):
            return sql
    return MOCK_FALLBACK


# --------------------------------------------------------------------------- #
# Live providers
# --------------------------------------------------------------------------- #

PROVIDER_ENV = {
    "asu": ("OPENCODE_API_KEY", "OPENCODE_BASE_URL"),
    "openai": ("OPENAI_API_KEY", "OPENAI_BASE_URL"),
}


def api_generate(provider: str, schema: str, question: str, model: str,
                 arm: str = DEFAULT_ARM, temperature: float = TEMPERATURE,
                 timeout: float = 180.0) -> Tuple[str, Optional[int], Optional[int]]:
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "The 'openai' package is required. Run: pip install -r requirements.txt"
        ) from exc

    key_var, url_var = PROVIDER_ENV[provider]
    api_key = os.getenv(key_var)
    base_url = os.getenv(url_var)

    if not api_key:
        raise RuntimeError(
            f"{key_var} is not set. Copy .env.example to .env and fill it in, "
            f"or run with --provider mock to use the offline stub."
        )
    if provider == "asu" and not base_url:
        raise RuntimeError(
            f"{url_var} is not set. The ASU OpenCode gateway needs an explicit "
            f"OpenAI-compatible base URL (for example https://<host>/v1)."
        )

    client = OpenAI(api_key=api_key, base_url=base_url or None, timeout=timeout)
    response = client.chat.completions.create(
        model=model,
        messages=build_messages(schema, question, arm=arm),
        temperature=temperature,
    )
    usage = getattr(response, "usage", None)
    return (
        response.choices[0].message.content or "",
        getattr(usage, "prompt_tokens", None),
        getattr(usage, "completion_tokens", None),
    )


def generate(provider: str, schema: str, question: str,
             model: Optional[str] = None, arm: str = DEFAULT_ARM) -> Generation:
    """Run one model call and return the parsed result plus its cost."""
    if arm not in ARMS:
        raise ValueError(f"Unknown arm: {arm}. Choose from {sorted(ARMS)}.")

    result = Generation(
        arm=arm,
        prompt_version=ARMS[arm]["version"],
        provider=provider,
        model=model or "",
    )

    if provider == "mock":
        result.raw = mock_generate(question, arm=arm)
        result.model = model or "mock"
    elif provider in PROVIDER_ENV:
        raw, ptok, ctok = api_generate(
            provider, schema, question, model or "gpt-4o-mini", arm=arm
        )
        result.raw, result.prompt_tokens, result.completion_tokens = raw, ptok, ctok
        result.model = model or "gpt-4o-mini"
    else:
        raise ValueError(f"Unknown provider: {provider}")

    refused, missing = parse_refusal(result.raw)
    result.refused, result.missing_field = refused, missing
    result.sql = "" if refused else strip_sql_fences(result.raw)
    return result


def generate_sql(provider: str, schema: str, question: str,
                 model: Optional[str] = None, arm: str = DEFAULT_ARM) -> str:
    """Backwards-compatible wrapper returning just the SQL string."""
    return generate(provider, schema, question, model=model, arm=arm).sql
