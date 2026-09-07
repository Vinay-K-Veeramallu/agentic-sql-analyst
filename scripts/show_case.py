"""Show what each arm did on one question, re-executing its SQL offline.

Used to produce the failure figure in the proposal, and useful on its own for
inspecting a single case without spending a model call:

    python scripts/show_case.py --id H3

For every committed trace that contains the question, this prints the arm, the
SQL it generated (or the refusal it emitted), the result table re-executed
against the deterministic database, and the scorer's verdict.
"""

import argparse
import glob
import json
import os
import sys
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import dataset  # noqa: E402
import db  # noqa: E402
import score  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--id", required=True, help="Question id, e.g. H3")
    parser.add_argument("--db", default=os.path.join(ROOT, "data", "analytics.db"))
    parser.add_argument("--results", default=os.path.join(ROOT, "eval", "results"))
    parser.add_argument("--max-rows", type=int, default=8)
    args = parser.parse_args()

    questions = dataset.index_by_id(dataset.load_questions())
    question = questions.get(args.id)
    if question is None:
        print(f"[ERROR] unknown question id {args.id!r}")
        return 1

    conn = db.connect(args.db)
    print(f"Question [{question.id}] ({question.set}/{question.kind}): "
          f"{question.question}")
    if question.kind == "unanswerable":
        print(f"Schema does not contain: {question.missing_field}  "
              f"-> the only correct behaviour is to refuse and name it")
    print()

    for path in sorted(glob.glob(os.path.join(args.results, "*.jsonl"))):
        for line in open(path, "r", encoding="utf-8"):
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("question_id") != question.id:
                continue
            if record.get("exclude_from_accuracy"):
                continue

            arm = record.get("arm")
            print(f"--- arm {arm} ({record.get('model')}, "
                  f"prompt {record.get('prompt_version')}) "
                  f"[{os.path.basename(path)}]")

            if record.get("refused"):
                print(f"    REFUSED: NOT_ANSWERABLE: {record.get('missing_field')}")
            elif record.get("sql"):
                for chunk in textwrap.wrap(" ".join(record["sql"].split()), 108):
                    print(f"    {chunk}")
                try:
                    frame = db.run_select(conn, record["sql"],
                                          max_rows=args.max_rows)
                    print()
                    for row in frame.to_string(index=False).splitlines():
                        print(f"    {row}")
                except Exception as exc:
                    print(f"    execution failed: {exc}")

            correct, verdict = score.score_record(question, record, conn)
            print(f"\n    verdict: {'PASS' if correct else 'FAIL'} - {verdict}\n")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
