"""The retrieval pass between steps 7 and 8: one run of the development
set through retrieval and the join tree, recorded, and the keep-rule
applied between two such runs.

    python -m app.show_pass --record NAME     writes eval/pass_07b/NAME.json
    python -m app.show_pass --compare BASE NAME

Retrieval only: no model is called, and every vector comes from the cache.
The sixteen questions of eval/questions.yaml are judged by what they hold
(app/core/keep_rule.py). Four development questions of step 7 are shown
beside them, by the tables their prompt would hold: d2, d3 and d6, whose
declines step 7 diagnosed, and d7, which diverged. The held-out set is not
read here or anywhere before step 10.
"""

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import yaml

from app.config import get_settings
from app.core.eval_set import parse_eval_set
from app.core.graph_builder import build_graph
from app.core.keep_rule import held, keep
from app.core.locate import locate
from app.shell.retrieval_session import open_retrieval
from app.shell.schema_ingestor import SchemaIngestor
from app.show_eval_step6 import produced

EVAL = Path(__file__).resolve().parents[1] / "eval"
RUNS = EVAL / "pass_07b"
RETURNS = ("store_returns", "catalog_returns", "web_returns")
# The table the step 7 diagnosis named as missing, per development question.
NAMED_MISSING = {"d2": "date_dim", "d3": "catalog_sales", "d6": "time_dim"}
SHOWN = ("d2", "d3", "d6", "d7")


def _attachments(tree) -> list[dict]:
    return [
        {
            "anchor": a.anchor,
            "to": a.attached_to,
            "rule": a.rule,
            "by": a.path.id,
            "tied": len(a.tied),
        }
        for a in tree.attachments
        if a.path is not None
    ]


def record(name: str) -> int:
    settings = get_settings()
    snapshot = SchemaIngestor(settings.warehouse_database_url, settings.warehouse_overlay_path).ingest()
    graph = build_graph(snapshot)
    opened = open_retrieval(snapshot, 0.5, 0.5, 5, "the retrieval pass")
    eval_set = parse_eval_set((EVAL / "questions.yaml").read_text())
    development = {q["id"]: q["question"] for q in yaml.safe_load((EVAL / "dev_questions.yaml").read_text())["questions"]}

    def run(text: str):
        scored = opened.index.score_question(text)
        began = time.monotonic()
        located = locate(text, scored.scores, scored.terms, graph, opened.settings)
        return located, (time.monotonic() - began) * 1000

    questions, core_ms = {}, []
    for question in eval_set.questions:
        located, took = run(question.question)
        core_ms.append(took)
        tree = located.tree
        expected = set(question.tables) | set(question.tables_one_of)
        found = set(located.tables)
        questions[str(question.id)] = {
            "held": sorted(held(question, produced(question, located))),
            "declined": located.declined,
            "anchors": list(located.retrieval.anchors),
            "tree": list(located.tables),
            "missing": sorted(set(question.tables) - found)
            + (["one of " + "/".join(question.tables_one_of)] if question.tables_one_of and not found & set(question.tables_one_of) else []),
            "surplus": [] if question.warning == "decline" else sorted(found - expected) + sorted(found & set(question.tables_one_of))[1:],
            "judged_tables": question.warning != "decline",
            "attachments": _attachments(tree),
            "warnings": sorted(located.warning_codes),
            "close_calls": len(located.explanation.close_calls) if not located.declined else 0,
        }
    shown = {}
    for name_, text in development.items():
        if name_ in SHOWN:
            located, took = run(text)
            core_ms.append(took)
            shown[name_] = {
                "anchors": list(located.retrieval.anchors),
                "tree": list(located.tables),
                "subgraph": list(located.tree.subgraph),
                "attachments": _attachments(located.tree),
                "warnings": sorted(located.warning_codes),
            }
    result = {
        "name": name,
        "questions": questions,
        "development": shown,
        "core_ms": {"median": round(statistics.median(core_ms)), "largest": round(max(core_ms))},
    }
    RUNS.mkdir(exist_ok=True)
    (RUNS / f"{name}.json").write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(f"recorded {name}: {len(questions)} questions, {len(shown)} development questions; "
          f"retrieval and tree median {result['core_ms']['median']} ms, largest {result['core_ms']['largest']} ms")  # fmt: skip
    return 0


def _to_returns(run: dict) -> list[str]:
    """Attachments the alphabet decided that hang a table on a returns
    table while that channel's sales table is in the tree (item 65)."""
    out = []
    for number, q in run["questions"].items():
        for a in q["attachments"]:
            if a["rule"] == "alphabetical" and a["to"] in RETURNS and a["to"].replace("returns", "sales") in q["tree"]:
                out.append(f"Q{number} {a['anchor']}")
    return out


def summary(run: dict) -> dict:
    judged = [q for q in run["questions"].values() if q["judged_tables"]]
    codes = sorted({code for q in run["questions"].values() for code in q["warnings"]})
    return {
        "every expected table": sum(not q["missing"] for q in judged),
        "of": len(judged),
        "tables missing": sum(len(q["missing"]) for q in judged),
        "tables beyond the expected": sum(len(q["surplus"]) for q in judged),
        "alphabet-decided attachments": sum(a["rule"] == "alphabetical" for q in run["questions"].values() for a in q["attachments"]),
        "of them onto a returns table beside its sales table": len(_to_returns(run)),
        "warnings": {code: sum(code in q["warnings"] for q in run["questions"].values()) for code in codes},
        "close calls": f"{sum(q['close_calls'] for q in run['questions'].values())} on "
                       f"{sum(q['close_calls'] > 0 for q in run['questions'].values())}",  # fmt: skip
    }


def compare(base_name: str, name: str) -> int:
    base = json.loads((RUNS / f"{base_name}.json").read_text())
    run = json.loads((RUNS / f"{name}.json").read_text())
    other = tuple(
        f"{d} is shown {table}"
        for d, table in NAMED_MISSING.items()
        if table in run["development"][d]["subgraph"] and table not in base["development"][d]["subgraph"]
    )
    verdict = keep(
        {int(n): frozenset(q["held"]) for n, q in base["questions"].items()},
        {int(n): frozenset(q["held"]) for n, q in run["questions"].items()},
        other,
    )
    print(f"{name} against {base_name}")
    print(f"  VERDICT: {'KEPT' if verdict.kept else 'NOT KEPT'}")
    for number, items in verdict.lost.items():
        print(f"  lost    Q{number}: {'; '.join(items)}")
    for number, items in verdict.gained.items():
        print(f"  gained  Q{number}: {'; '.join(items)}")
    for gain in verdict.other_gains:
        print(f"  gained  {gain}")
    before, after = summary(base), summary(run)
    for key in before:
        if key == "of":
            continue
        print(f"  {key}: {before[key]} -> {after[key]}" + (f" of {after['of']}" if key == "every expected table" else ""))
    print(f"  returns attachments by the alphabet: {', '.join(_to_returns(base)) or 'none'} -> {', '.join(_to_returns(run)) or 'none'}")
    print(f"  retrieval and tree: median {run['core_ms']['median']} ms, largest {run['core_ms']['largest']} ms (NFR-02: 1000 ms)")
    for number in sorted(run["questions"], key=int):
        b, r = base["questions"][number], run["questions"][number]
        if b["tree"] != r["tree"] or b["attachments"] != r["attachments"] or b["warnings"] != r["warnings"]:
            print(f"  Q{number} tree     {', '.join(b['tree'])}")
            print(f"  {'':<{len(number) + 1}} now      {', '.join(r['tree'])}")
            if b["warnings"] != r["warnings"]:
                print(f"  {'':<{len(number) + 1}} warnings {', '.join(b['warnings']) or 'none'} -> {', '.join(r['warnings']) or 'none'}")
            for a in r["attachments"]:
                if a not in b["attachments"]:
                    print(f"  {'':<{len(number) + 1}} attached {a['anchor']} to {a['to']} [{a['rule']}, {a['tied']} tied] by {a['by']}")
    for d in SHOWN:
        b, r = base["development"][d], run["development"][d]
        if b == r:
            print(f"  {d} unchanged: shown {', '.join(r['subgraph'])}")
        else:
            print(f"  {d} shown    {', '.join(b['subgraph'])}")
            print(f"     now      {', '.join(r['subgraph'])}")
            for a in r["attachments"]:
                if a not in b["attachments"]:
                    print(f"     attached {a['anchor']} to {a['to']} [{a['rule']}, {a['tied']} tied] by {a['by']}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.show_pass", description=__doc__.splitlines()[0])
    parser.add_argument("--record", metavar="NAME")
    parser.add_argument("--compare", nargs=2, metavar=("BASE", "NAME"))
    arguments = parser.parse_args(argv)
    if arguments.record:
        return record(arguments.record)
    if arguments.compare:
        return compare(*arguments.compare)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
