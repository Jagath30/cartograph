"""The smoke check's two end-to-end lines (NFR-27, criterion 13).

    docker compose exec backend python -m app.smoke_pipeline

One fixed development question goes through the whole pipeline: one paid
model call. Then two things are checked, and each prints one line that
scripts/smoke.sh reads:

  PIPELINE   the question was answered: rows came back, and the trace
             holds every section a full run produces.
  WITNESS    the trace reports the joins the executed SQL actually made.
             The witness is Postgres and not this application: EXPLAIN
             (without ANALYZE, so nothing runs) is asked for its plan of
             the very text the trace says was executed, the join
             conditions in that plan are gathered into equivalence
             classes, and those must equal the classes of the trace's
             actual_edges. The text validated and the text executed must
             also be the same text.

A MODEL THAT DIVERGES DOES NOT FAIL THIS. The WITNESS line compares what
the trace says the SQL joined with what Postgres says it joins; whether
that is the path selected is not asked. A TRACE THAT MISREPORTS DOES.

It witnesses this one question and is never part of the evaluation. If
it ever disagrees because the planner rewrote a join, and not because of
a fault here, the owner's ruling is to fall back to the hash match alone
and record why.

Exit: 0 both pass; 1 either fails; 3 no OPENAI_API_KEY (a clean skip).
"""

import json
import logging
import re
import sys

import psycopg

from app.answer_report import summary
from app.config import get_settings
from app.shell.embedder import EmbeddingKeyMissing
from app.shell.model_client import ModelKeyMissing
from app.shell.pipeline_session import open_pipeline

# Development question d4 of eval/dev_questions.yaml: one join, with two
# keys to choose between. Not an evaluation question. (d3 was first named
# for this and was declined in development run 1: retrieval did not bring
# catalog_sales.)
#
# THE PIPELINE LINE IS STRICT: it needs "answered". The model does not
# repeat itself exactly at temperature 0, so a decline is possible; it
# fails the check and says that the model declined. No retry.
QUESTION = "How do purchases on the web split by the buyer's level of education?"

NO_KEY_EXIT = 3
CONDITIONS = ("Hash Cond", "Merge Cond", "Join Filter", "Index Cond", "Recheck Cond")
_EQUALITY = re.compile(r"\(?\(?(\w+)\.(\w+)\)?(?:::\w+)? = \(?(\w+)\.(\w+)\)?(?:::\w+)?\)?")


def classes(pairs) -> set[frozenset]:
    """Pairs of columns declared equal, gathered into classes."""
    groups: list[set] = []
    for a, b in pairs:
        touching = [group for group in groups if a in group or b in group]
        merged = {a, b}.union(*touching)
        groups = [group for group in groups if group not in touching] + [merged]
    return {frozenset(group) for group in groups}


def plan_joins(plan: dict) -> set[frozenset]:
    """The join conditions of an EXPLAIN (VERBOSE, FORMAT JSON) plan, as
    classes of (table, column). VERBOSE qualifies every column."""
    aliases: dict[str, str] = {}
    conditions: list[str] = []

    def walk(node: dict) -> None:
        if "Relation Name" in node:
            aliases[node.get("Alias", node["Relation Name"])] = node["Relation Name"]
        conditions.extend(node[key] for key in CONDITIONS if key in node)
        for child in node.get("Plans", []):
            walk(child)

    walk(plan)
    pairs = []
    for condition in conditions:
        for left_alias, left, right_alias, right in _EQUALITY.findall(condition):
            if left_alias in aliases and right_alias in aliases:
                pairs.append(((aliases[left_alias], left), (aliases[right_alias], right)))
    return classes(pairs)


def witness(sql: str, warehouse_url: str) -> set[frozenset]:
    with psycopg.connect(warehouse_url, autocommit=True) as connection:
        # The executor's own protocol: one statement, nothing bound into it.
        (plan,) = connection.execute("EXPLAIN (VERBOSE, FORMAT JSON) " + sql, prepare=False).fetchone()
    return plan_joins(plan[0]["Plan"])


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(message)s", stream=sys.stderr)
    try:
        pipeline = open_pipeline("smoke_pipeline", "smoke")
        trace = pipeline.orchestrator.answer(QUESTION)
    except (ModelKeyMissing, EmbeddingKeyMissing) as missing:
        print(f"NO KEY  {missing}")
        return NO_KEY_EXIT
    found = summary(trace)
    failed = False

    sections = {
        "prompt": trace.prompt is not None,
        "attempts": bool(trace.attempts),
        "validation": trace.validation is not None,
        "conformance": trace.conformance is not None,
        "execution": trace.execution is not None,
        "timings": {"locate", "prompt", "generate", "validate", "conform", "execute"} <= set(trace.timings),
    }
    missing = [name for name, present in sections.items() if not present]
    if trace.outcome == "answered" and found["row_count"] and not missing:
        print(f"PIPELINE PASS  answered; {found['row_count']} rows; conformance {found['conformance']}; "
              f"{len(trace.attempts)} model call(s), ${trace.cost_usd:.6f}; model {trace.attempts[-1].reply.returned_model}")  # fmt: skip
    elif trace.outcome == "not_answerable" and trace.attempts:
        # Strict on purpose: a check that can pass without running the whole
        # pipeline is decorative. But the cause is said, so that the model
        # declining is never mistaken for a fault in the code.
        failed = True
        print("PIPELINE FAIL  THE MODEL DECLINED the smoke question (it replied not_answerable). This is the "
              f"model's answer and not a code fault; nothing was validated or run. It was shown: {', '.join(found['prompt_tables'])}")  # fmt: skip
    else:
        failed = True
        print(f"PIPELINE FAIL  outcome {trace.outcome} ({trace.message}); rows {found['row_count']}; missing sections {missing}")

    if trace.execution is None or not trace.execution.ok:
        print("WITNESS FAIL  no SQL was executed, so there is nothing to witness")
        return 1
    same_text = trace.validated_sha256 == trace.executed_sha256
    reported = classes((tuple(edge["left"]), tuple(edge["right"])) for edge in found["actual_edges"])
    seen = witness(trace.execution.executed, get_settings().warehouse_database_url)

    def shown(groups) -> str:
        return "; ".join(sorted(" = ".join(sorted(f"{t}.{c}" for t, c in group)) for group in groups)) or "no joins"

    if same_text and reported == seen:
        print(f"WITNESS PASS  the trace reports the joins Postgres plans for the executed SQL: {shown(seen)}"
              + ("  (the SQL diverged from the selected path, and says so)" if trace.diverged else ""))  # fmt: skip
    else:
        failed = True
        print(f"WITNESS FAIL  validated and executed text the same: {same_text}; the trace reports [{shown(reported)}]; "
              f"Postgres plans [{shown(seen)}]")  # fmt: skip
    print(json.dumps({"sql": " ".join(trace.execution.executed.split()), "ledger_total_usd": pipeline.ledger.total()}))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
