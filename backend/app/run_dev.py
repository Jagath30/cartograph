"""Run the development questions of step 7 through the whole pipeline.

    docker compose exec backend python -m app.run_dev --run 1
    docker compose exec backend python -m app.run_dev --compare 1 2

`--run N` puts each of the eight questions of eval/dev_questions.yaml
through `app.ask`'s pipeline once, prints each trace, and keeps the run in
eval/dev_runs/runN.txt (what was printed) and runN.json (each trace's
summary). `--compare A B` says for how many questions two runs produced
the same SQL, byte for byte.

THE EVALUATION SET IS NOT READ HERE and no question of it is sent. These
eight are for developing the prompt and are judged by eye; nothing is
scored.

Paid: about eight model calls a run. Every call goes to the spend ledger.
"""

import argparse
import contextlib
import io
import json
import logging
import sys
from pathlib import Path

import yaml

from app.answer_report import print_trace, summary
from app.shell.pipeline_session import CEILING_USD, open_pipeline

EVAL = Path(__file__).resolve().parents[1] / "eval"
QUESTIONS = EVAL / "dev_questions.yaml"
RUNS = EVAL / "dev_runs"


def run(number: int) -> int:
    questions = yaml.safe_load(QUESTIONS.read_text())["questions"]
    pipeline = open_pipeline("run_dev", f"dev run {number}")
    before = pipeline.ledger.total()
    summaries, printed = [], io.StringIO()
    for entry in questions:
        trace = pipeline.orchestrator.answer(entry["question"])
        summaries.append({"id": entry["id"], **summary(trace)})
        with contextlib.redirect_stdout(printed):
            print(f"\n {entry['id']}  {entry['exercises'].strip()}")
            print_trace(trace)
    outcomes = ", ".join(f"{s['id']} {s['outcome']}" for s in summaries)
    footer = (
        f"\n    run {number}: {outcomes}\n"
        f"    model spend this run ${pipeline.ledger.total() - before:.6f}; "
        f"embedded now {pipeline.retrieval.paid.texts} texts, ${pipeline.retrieval.paid.cost_usd:.8f}; "
        f"ledger total ${pipeline.ledger.total():.6f} of a ceiling of ${CEILING_USD:.2f}\n"
    )
    RUNS.mkdir(exist_ok=True)
    (RUNS / f"run{number}.txt").write_text(printed.getvalue() + footer)
    (RUNS / f"run{number}.json").write_text(json.dumps(summaries, indent=1) + "\n")
    print(printed.getvalue() + footer)
    return 0


def compare(first: int, second: int) -> int:
    a = {s["id"]: s for s in json.loads((RUNS / f"run{first}.json").read_text())}
    b = {s["id"]: s for s in json.loads((RUNS / f"run{second}.json").read_text())}
    same = 0
    for key in a:
        attempts_a = [attempt["sql"] for attempt in a[key]["attempts"]]
        attempts_b = [attempt["sql"] for attempt in b[key]["attempts"]]
        identical = a[key]["sql"] == b[key]["sql"] and a[key]["outcome"] == b[key]["outcome"]
        same += identical
        print(f"    {key}  {'identical' if identical else 'DIFFERENT'}  outcome {a[key]['outcome']} / {b[key]['outcome']}; "
              f"conformance {a[key]['conformance']} / {b[key]['conformance']}; "
              f"every attempt identical: {attempts_a == attempts_b}")  # fmt: skip
    print(f"    {same} of {len(a)} questions produced the same final SQL and outcome in run {first} and run {second}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="The development questions through the whole pipeline.")
    parser.add_argument("--run", type=int, help="run the eight questions and keep the run under this number")
    parser.add_argument("--compare", type=int, nargs=2, help="compare the SQL of two kept runs")
    arguments = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(message)s", stream=sys.stderr)
    if arguments.compare:
        return compare(*arguments.compare)
    if arguments.run is None:
        parser.error("give --run N or --compare A B")
    return run(arguments.run)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
