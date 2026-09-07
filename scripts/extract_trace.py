"""Rebuild a machine-readable trace from the committed human-readable run log.

The 2026-09-06 live runs predate structured tracing, so the only primary record
of them is ``docs/baseline_run_output.txt``. This script converts that log into
the same JSONL schema ``src/baseline.py --trace`` now emits, so those runs can be
scored by ``src/score.py`` like any other.

    python scripts/extract_trace.py \
        --log docs/baseline_run_output.txt \
        --out eval/results/b0_live_20260906.jsonl

Provenance and honesty notes
  * Question ids are resolved by exact text match against eval/questions.yaml.
  * Token counts are null: the runs were not instrumented for usage at the time.
  * The log contains 11 runs over 10 distinct questions. The repeat of E1
    returned in 0.86s against 13.41s for identical SQL, which indicates a
    gateway-side cache hit rather than a system measurement; it is emitted with
    exclude_from_latency and exclude_from_accuracy set, so it is visible in the
    trace but never counted.
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

import dataset  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TS = r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+"
RE_HEADER = re.compile(TS + r" INFO Provider=(\S+) model=(\S+) db=(\S+)")
RE_QUESTION = re.compile(TS + r" INFO Question: (.+)")
RE_SQL = re.compile(TS + r" INFO Generated SQL: (.+)")
RE_OK = re.compile(TS + r" INFO Execution succeeded in ([\d.]+)s, (\d+) row")
RE_ERR = re.compile(TS + r" (?:ERROR) (.+)")


def parse(log_path: str):
    provider = model = None
    records, current = [], None

    with open(log_path, "r", encoding="utf-8") as handle:
        for line in handle:
            header = RE_HEADER.match(line)
            if header:
                provider, model = header.group(2), header.group(3)
                continue

            question = RE_QUESTION.match(line)
            if question:
                if current:
                    records.append(current)
                current = {
                    "ts": question.group(1),
                    "question": question.group(2).strip(),
                    "provider": provider,
                    "model": model,
                    "sql": "",
                    "exec_ok": False,
                    "rows": None,
                    "latency_ms": None,
                    "error_type": None,
                }
                continue

            sql = RE_SQL.match(line)
            if sql and current:
                current["sql"] = sql.group(2).strip()
                continue

            ok = RE_OK.match(line)
            if ok and current:
                current["exec_ok"] = True
                current["latency_ms"] = round(float(ok.group(2)) * 1000, 1)
                current["rows"] = int(ok.group(3))
                continue

            err = RE_ERR.match(line)
            if err and current:
                current["error_type"] = "LoggedError"
                current["error"] = err.group(2).strip()

    if current:
        records.append(current)
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--log", default=os.path.join(ROOT, "docs",
                                                      "baseline_run_output.txt"))
    parser.add_argument("--out", default=os.path.join(ROOT, "eval", "results",
                                                      "b0_live_20260906.jsonl"))
    parser.add_argument("--questions", default=dataset.DEFAULT_QUESTIONS)
    args = parser.parse_args()

    questions = dataset.load_questions(args.questions)
    by_text = {q.question.strip().lower(): q for q in questions}

    parsed = parse(args.log)
    if not parsed:
        print(f"[ERROR] no runs found in {args.log}")
        return 1

    seen, out_records, unmatched = set(), [], []
    for item in parsed:
        question = by_text.get(item["question"].strip().lower())
        if question is None:
            unmatched.append(item["question"])
            continue

        duplicate = question.id in seen
        seen.add(question.id)

        record = {
            "run_id": "log-20260906",
            "ts": item["ts"],
            "question_id": question.id,
            "question": item["question"],
            "arm": "b0",
            "prompt_version": "b0-v1",
            "provider": item["provider"],
            "model": item["model"],
            "temperature": 0.0,
            "sql": item["sql"],
            "refused": False,
            "missing_field": None,
            "assumptions": None,
            "exec_ok": item["exec_ok"],
            "rows": item["rows"],
            "truncated": False,
            "error_type": item["error_type"],
            "prompt_tokens": None,
            "completion_tokens": None,
            "latency_ms": item["latency_ms"],
            "source": "docs/baseline_run_output.txt via scripts/extract_trace.py",
        }
        if duplicate:
            record["exclude_from_latency"] = True
            record["exclude_from_accuracy"] = True
            record["exclude_reason"] = (
                "repeat of the same question; 0.86s vs 13.41s for identical SQL "
                "indicates a gateway-side cache hit, not a system measurement"
            )
        out_records.append(record)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        for record in out_records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    scored = sum(1 for r in out_records if not r.get("exclude_from_accuracy"))
    print(f"[OK] wrote {len(out_records)} record(s) to "
          f"{os.path.relpath(args.out, ROOT)}")
    print(f"     {scored} scored, {len(out_records) - scored} excluded as duplicates")
    for text in unmatched:
        print(f"[WARN] no question id matched: {text!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
