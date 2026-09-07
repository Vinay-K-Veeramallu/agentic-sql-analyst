"""QueryMind baseline: a single-call Text-to-SQL runner.

Pipeline
--------
1. Connect to the SQLite analytics database (read-only).
2. Introspect the schema with PRAGMA table_info / foreign_key_list.
3. Render schema + question into one prompt for the selected arm.
4. Make ONE model call.
5. Guard the reply: single read-only SELECT/WITH, cost-bounded.
6. Execute, print the result table, and append a structured trace record.

There is deliberately no retry or self-correction loop: if step 4 or 6 fails, the
script reports the error and moves on. Closing that gap is the point of the
agentic system this baseline is measured against.

Two metrics are reported and must not be conflated:
  * Execution success - the query ran without a database error.
  * Answer accuracy   - the result set matches a reference query. Computed by
                        src/score.py from the trace this script writes, never here.

Arms: --arm b0 (frozen baseline) | b1 (b0 + permission to refuse).
"""

import argparse
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dataset  # noqa: E402
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
    "arm": llm.DEFAULT_ARM,
}


def load_config(path: str) -> dict:
    config = dict(DEFAULTS)
    if not os.path.exists(path):
        return config
    try:
        import yaml
    except ImportError as exc:  # fail loudly: a silent default is worse
        raise RuntimeError(
            "PyYAML is required to read config/settings.yaml. "
            "Run: pip install -r requirements.txt"
        ) from exc
    with open(path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    unknown = sorted(set(data) - set(DEFAULTS))
    if unknown:
        raise ValueError(f"Unknown key(s) in {path}: {unknown}")
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
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
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
    """Return a list of (question_id, question_text)."""
    if args.query:
        return [(args.id or "adhoc", args.query)]
    if args.questions:
        chosen = dataset.select(
            dataset.load_questions(args.questions),
            sets=args.set.split(",") if args.set else None,
            ids=args.ids.split(",") if args.ids else None,
        )
        if not chosen:
            raise SystemExit("No questions matched --set/--ids.")
        return [(q.id, q.question) for q in chosen]
    if args.input:
        with open(args.input, "r", encoding="utf-8") as handle:
            lines = [ln.strip() for ln in handle
                     if ln.strip() and not ln.startswith("#")]
        stem = os.path.splitext(os.path.basename(args.input))[0]
        return [(f"{stem}:{i + 1}", text) for i, text in enumerate(lines)]
    raise SystemExit("Provide --query, --input, or --questions.")


class TraceWriter:
    """Append-only JSONL trace: one record per question, machine-readable."""

    def __init__(self, path, run_id):
        self.path = path
        self.run_id = run_id
        if path:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    def write(self, record: dict) -> None:
        if not self.path:
            return
        record = {"run_id": self.run_id, **record}
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def answer_one(conn, schema, question_id, question, config, logger, trace) -> bool:
    """Run the baseline on one question. Returns True when execution succeeded."""
    logger.info("[%s] Question: %s", question_id, question)
    started = time.perf_counter()

    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "question_id": question_id,
        "question": question,
        "arm": config["arm"],
        "prompt_version": llm.ARMS[config["arm"]]["version"],
        "provider": config["provider"],
        "model": config["model"],
        "temperature": llm.TEMPERATURE,
        "sql": "",
        "refused": False,
        "missing_field": None,
        "assumptions": None,
        "exec_ok": False,
        "rows": None,
        "truncated": False,
        "error_type": None,
        "error": None,
        "prompt_tokens": None,
        "completion_tokens": None,
        "latency_ms": None,
    }

    try:
        generation = llm.generate(
            config["provider"], schema, question,
            model=config["model"], arm=config["arm"],
        )
    except Exception as exc:
        record["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
        record["error_type"] = type(exc).__name__
        record["error"] = str(exc)[:500]
        logger.error("Generation failed (%s): %s", type(exc).__name__, exc)
        trace.write(record)
        return False

    record["prompt_tokens"] = generation.prompt_tokens
    record["completion_tokens"] = generation.completion_tokens
    record["sql"] = generation.sql
    record["refused"] = generation.refused
    record["missing_field"] = generation.missing_field

    if generation.refused:
        record["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
        logger.info("Refused as unanswerable; missing field: %s",
                    generation.missing_field)
        print(f"\nNOT ANSWERABLE from this schema - missing: "
              f"{generation.missing_field}\n")
        trace.write(record)
        return True

    logger.info("Generated SQL: %s", " ".join(generation.sql.split()))

    try:
        frame = db.run_select(conn, generation.sql, max_rows=int(config["max_rows"]))
    except (db.UnsafeQueryError, db.QueryBudgetError) as exc:
        record["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
        record["error_type"] = type(exc).__name__
        record["error"] = str(exc)[:500]
        logger.error("Rejected query (%s): %s", type(exc).__name__, exc)
        trace.write(record)
        return False
    except Exception as exc:
        record["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
        record["error_type"] = type(exc).__name__
        record["error"] = str(exc)[:500]
        logger.error("SQL execution failed (%s): %s", type(exc).__name__, exc)
        trace.write(record)
        return False

    elapsed_ms = (time.perf_counter() - started) * 1000
    record.update({
        "exec_ok": True,
        "rows": len(frame),
        "truncated": bool(frame.attrs.get("truncated")),
        "latency_ms": round(elapsed_ms, 1),
    })
    logger.info("Execution succeeded in %.2fs, %d row(s) returned%s",
                elapsed_ms / 1000.0, len(frame),
                " [TRUNCATED at max_rows]" if record["truncated"] else "")
    print()
    print(frame.to_string(index=False) if not frame.empty else "(no rows)")
    if record["truncated"]:
        print(f"\n[WARNING] output truncated at max_rows={config['max_rows']}; "
              f"this is not the complete result set.")
    print()
    trace.write(record)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="QueryMind baseline: natural language question -> SQL -> result."
    )
    source = parser.add_argument_group("question source")
    source.add_argument("--query", help="A single natural language question.")
    source.add_argument("--id", help="Question id to record for --query.")
    source.add_argument("--input", help="File with one question per line.")
    source.add_argument("--questions", nargs="?", const=dataset.DEFAULT_QUESTIONS,
                        help="Run the evaluation set (default eval/questions.yaml).")
    source.add_argument("--set", help="Filter --questions by set: easy,hard,...")
    source.add_argument("--ids", help="Filter --questions by id: E1,H3,...")

    parser.add_argument("--provider", choices=["asu", "openai", "mock"],
                        help="Override the provider from config/settings.yaml.")
    parser.add_argument("--arm", choices=sorted(llm.ARMS),
                        help="Prompt arm: b0 (frozen) or b1 (may refuse).")
    parser.add_argument("--model", help="Override the model name.")
    parser.add_argument("--db", help="Override the SQLite database path.")
    parser.add_argument("--max-rows", type=int, help="Row cap for printed results.")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="Config file path.")
    parser.add_argument("--trace", help="Append a JSONL trace record per question.")
    args = parser.parse_args()

    load_env()
    logger = setup_logging()
    config = load_config(args.config)
    for key, value in (("provider", args.provider), ("model", args.model),
                       ("database_path", args.db), ("arm", args.arm),
                       ("max_rows", args.max_rows)):
        if value:
            config[key] = value

    pd.set_option("display.width", 140)
    pd.set_option("display.max_columns", 25)

    questions = read_questions(args)
    run_id = uuid.uuid4().hex[:12]
    trace = TraceWriter(args.trace, run_id)

    logger.info("run_id=%s provider=%s model=%s arm=%s (%s) db=%s",
                run_id, config["provider"], config["model"], config["arm"],
                llm.ARMS[config["arm"]]["description"], config["database_path"])

    try:
        conn = db.connect(str(config["database_path"]))
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 2

    schema, tables = db.get_schema_string(conn)
    logger.info("Schema loaded successfully for tables: %s", tables)

    executed = 0
    for question_id, question in questions:
        if answer_one(conn, schema, question_id, question, config, logger, trace):
            executed += 1

    conn.close()

    if len(questions) > 1:
        logger.info("Execution success: %d/%d (%.1f%%) "
                    "- answer accuracy is scored separately by src/score.py",
                    executed, len(questions), 100.0 * executed / len(questions))
    if args.trace:
        logger.info("Trace written to %s", args.trace)

    return 0 if executed == len(questions) else 1


if __name__ == "__main__":
    sys.exit(main())
