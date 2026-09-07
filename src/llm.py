"""Text-to-SQL generation providers.

Three providers are supported so that the baseline is always reproducible:

* ``asu``    - ASU's OpenCode gateway (OpenAI-compatible chat completions API).
* ``openai`` - the public OpenAI API.
* ``mock``   - a deterministic, offline, rule-based stub that needs no API key.

``mock`` exists purely so a grader without credentials can still execute the
pipeline end to end. It is NOT the system under study.
"""

import os
import re
from typing import Optional

SYSTEM_PROMPT = """You are a senior data analyst who writes SQLite SQL.

Rules:
1. Answer with a single SQL query and nothing else. No prose, no explanation.
2. Use only the tables and columns given in the schema. Never invent names.
3. The query must be read-only: SELECT or WITH only.
4. Do not terminate the query with a semicolon.
5. Prefer explicit JOINs on the declared foreign keys.
6. Give aggregated columns a readable alias.
"""

USER_TEMPLATE = """Database schema:

{schema}

Question: {question}

SQL:"""


def build_messages(schema: str, question: str):
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_TEMPLATE.format(schema=schema, question=question)},
    ]


def strip_sql_fences(text: str) -> str:
    """Remove markdown code fences and stray prose that models like to add."""
    text = text.strip()
    fence = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1)
    text = re.sub(r"^\s*(sql|SQL)\s*:\s*", "", text.strip())

    match = re.search(r"\b(SELECT|WITH)\b", text, re.IGNORECASE)
    if match:
        text = text[match.start():]
    return text.strip().rstrip(";").strip()


# --------------------------------------------------------------------------- #
# Offline rule-based stub
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

MOCK_FALLBACK = "SELECT COUNT(*) AS total_orders FROM orders"


def mock_generate(question: str) -> str:
    lowered = question.lower()
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
                 temperature: float = 0.0, timeout: float = 60.0) -> str:
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
        messages=build_messages(schema, question),
        temperature=temperature,
    )
    return response.choices[0].message.content or ""


def generate_sql(provider: str, schema: str, question: str,
                 model: Optional[str] = None) -> str:
    """Return a cleaned SQL string for `question` given `schema`."""
    if provider == "mock":
        return mock_generate(question)
    if provider not in PROVIDER_ENV:
        raise ValueError(f"Unknown provider: {provider}")
    raw = api_generate(provider, schema, question, model or "gpt-4o-mini")
    return strip_sql_fences(raw)
