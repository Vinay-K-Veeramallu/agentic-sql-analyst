"""QueryMind baseline: a single-call Text-to-SQL script.

Pipeline
--------
1. Connect to the SQLite analytics database.
2. Introspect the schema with PRAGMA table_info / foreign_key_list.
3. Render schema + question into one prompt.
4. Make ONE model call to obtain a SQL string.
5. Reject anything that is not a read-only SELECT.
6. Execute and print the result table.

There is deliberately no retry or self-correction loop: if step 4 or 6 fails,
the script reports the error and exits non-zero. Closing that gap is the point
of the agentic system this baseline is measured against.
"""

import argparse
import logging
import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db  # noqa: E402
import llm  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(ROOT, "data", "analytics.db")
DEFAULT_CONFIG = os.path.join(ROOT, "config", "settings.yaml")
LOG_PATH = os.path.join(ROOT, "logs", "baseline_run.log")

DEFAULTS = {
    "provider": "mock",
    "model": "muse-glimmer-30b",
    "database_path": DEFAULT_DB,
    "max_rows": 50,
}


def load_config(path: str) -> dict:
    config = dict(DEFAULTS)
    if not os.path.exists(path):
        return config
    try:
        import yaml
    except ImportError:
        return config
    with open(path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    for key in DEFAULTS:
        if key in data and data[key] is not None:
            config[key] = data[key]
    if not os.path.isabs(str(config["database_path"])):
        config["database_path"] = os.path.join(ROOT, str(config["database_path"]))
    return config


def setup_logging() -> logging.Logger:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    logger = logging.getLogger("querymind")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    )
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(stream_handler)
    return logger


def load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(os.path.join(ROOT, ".env"))


def read_questions(args) -> list:
    if args.query:
        return [args.query]
    if args.input:
        with open(args.input, "r", encoding="utf-8") as handle:
            lines = [ln.strip() for ln in handle if ln.strip() and not ln.startswith("#")]
        return lines
    raise SystemExit("Provide either --query or --input.")


def answer_one(conn, schema, question, config, logger) -> bool:
    """Run the full baseline on one question. Returns True on success."""
    logger.info("Question: %s", question)
    started = time.time()

    try:
        sql = llm.generate_sql(
            config["provider"], schema, question, model=config["model"]
        )
    except Exception as exc:
        logger.error("SQL generation failed: %s", exc)
        return False

    logger.info("Generated SQL: %s", " ".join(sql.split()))

    try:
        frame = db.run_select(conn, sql, max_rows=int(config["max_rows"]))
    except db.UnsafeQueryError as exc:
        logger.error("Rejected unsafe query: %s", exc)
        return False
    except Exception as exc:
        logger.error("SQL execution failed: %s", exc)
        return False

    elapsed = time.time() - started
    logger.info("Execution succeeded in %.2fs, %d row(s) returned", elapsed, len(frame))
    print()
    print(frame.to_string(index=False) if not frame.empty else "(no rows)")
    print()
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="QueryMind baseline: natural language question -> SQL -> result."
    )
    parser.add_argument("--query", help="A single natural language question.")
    parser.add_argument("--input", help="File containing one question per line.")
    parser.add_argument("--provider", choices=["asu", "openai", "mock"],
                        help="Override the provider from config/settings.yaml.")
    parser.add_argument("--model", help="Override the model name.")
    parser.add_argument("--db", help="Override the SQLite database path.")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="Config file path.")
    args = parser.parse_args()

    load_env()
    logger = setup_logging()
    config = load_config(args.config)
    for key, value in (("provider", args.provider), ("model", args.model),
                       ("database_path", args.db)):
        if value:
            config[key] = value

    pd.set_option("display.width", 120)
    pd.set_option("display.max_columns", 20)

    questions = read_questions(args)
    logger.info("Provider=%s model=%s db=%s", config["provider"], config["model"],
                config["database_path"])

    try:
        conn = db.connect(str(config["database_path"]))
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 2

    schema, tables = db.get_schema_string(conn)
    logger.info("Schema loaded successfully for tables: %s", tables)

    successes = 0
    for question in questions:
        if answer_one(conn, schema, question, config, logger):
            successes += 1

    conn.close()

    if len(questions) > 1:
        rate = 100.0 * successes / len(questions)
        logger.info("Baseline success rate: %d/%d (%.1f%%)",
                    successes, len(questions), rate)

    return 0 if successes == len(questions) else 1


if __name__ == "__main__":
    sys.exit(main())
