"""Run the evaluation set's checks against the PathFinder, and report.

    docker compose exec backend python -m app.show_eval

For every check in backend/eval/questions.yaml: find the path between its
two tables on the live warehouse, and say whether the path selected and the
warnings raised are the ones the set expects (FR-37, as far as step 5 can
take it). Each line is one of

    MATCH               the set and the system agree
    MISMATCH            they do not
    EXPECTED FAILURE    the set said in advance this would fail: CONFIRMED
                        if it did, UNEXPECTEDLY PASSED if it did not
    NOT EVALUABLE       nothing at this step can speak for the question

A REPORT, NOT A TEST. It exits 0 whatever it finds, because disagreement is
expected and is what the next step is measured against. It changes nothing:
when the system disagrees with the set, the line says so and both stay as
they are (DR-16).

No logic lives here: ingest (shell), build, find, explain, judge (core),
print.
"""

import hashlib
import textwrap
from collections import Counter
from pathlib import Path

from app.config import get_settings
from app.core.eval_set import (
    EXPECTED_FAILURE_CONFIRMED,
    EXPECTED_FAILURE_UNEXPECTEDLY_PASSED,
    MATCH,
    MISMATCH,
    NO_MECHANISM,
    NOT_EVALUABLE,
    PARTIAL,
    Check,
    edges_of,
    judge,
    judge_question,
    parse_eval_set,
)
from app.core.explainer import explain
from app.core.graph_builder import build_graph
from app.core.path_finder import DEFAULT_MAX_JOINS, find_paths
from app.shell.schema_ingestor import SchemaIngestor

EVAL_SET = Path(__file__).resolve().parents[1] / "eval" / "questions.yaml"

_LABEL = {
    MATCH: "MATCH",
    MISMATCH: "MISMATCH",
    EXPECTED_FAILURE_CONFIRMED: "EXPECTED FAILURE CONFIRMED",
    EXPECTED_FAILURE_UNEXPECTEDLY_PASSED: "EXPECTED FAILURE UNEXPECTEDLY PASSED",
    NOT_EVALUABLE: "NOT EVALUABLE AT THIS STEP",
}
_ORDER = (MATCH, MISMATCH, EXPECTED_FAILURE_CONFIRMED, EXPECTED_FAILURE_UNEXPECTEDLY_PASSED, NOT_EVALUABLE)


def _pairs(label: str, edges) -> None:
    """One column pair per line, the label on the first."""
    for number, (start, end) in enumerate(sorted(edges)):
        print(f"{label if number == 0 else '':>19}  {start} = {end}")


def _codes(codes) -> str:
    return ", ".join(sorted(codes)) or "none"


def _wrapped(label: str, text: str) -> None:
    lines = textwrap.wrap(text, width=66)
    print(f"{label:<12}{lines[0]}")
    for line in lines[1:]:
        print(f"{'':<12}{line}")


def _tally(statuses: list[str]) -> str:
    counts = Counter(statuses)
    return ", ".join(f"{counts[status]} {_LABEL[status].lower()}" for status in _ORDER if counts[status])


def _expected(check: Check) -> None:
    for number, path in enumerate(check.accept):
        _pairs("expected" if number == 0 else "or", edges_of(path))
    print(f"{'':>19}  warnings: {_codes(check.warnings)}")


def main() -> None:
    text = EVAL_SET.read_bytes()
    eval_set = parse_eval_set(text.decode())

    settings = get_settings()
    snapshot = SchemaIngestor(settings.warehouse_database_url, settings.warehouse_overlay_path).ingest()
    graph = build_graph(snapshot)
    sources = Counter(key.source for key in snapshot.foreign_keys)

    print(f"{'set':<12}backend/eval/{EVAL_SET.name}")
    print(f"{'sha256':<12}{hashlib.sha256(text).hexdigest()}")
    _wrapped("provenance", eval_set.provenance)
    _wrapped("derivation", eval_set.derivation)
    print(
        f"{'graph':<12}{len(snapshot.foreign_keys)} foreign keys "
        f"({sources['catalog']} catalog, {sources['overlay']} overlay); "
        f"routes of up to {DEFAULT_MAX_JOINS} joins"
    )

    check_statuses: list[str] = []
    question_statuses: list[str] = []

    for question in eval_set.questions:
        print(f"\n{question.id:>2}  {question.question}")
        mechanism = "" if eval_set.warning_categories[question.warning] != NO_MECHANISM else " (no mechanism yet)"
        print(f"    expects warning: {question.warning}{mechanism}; fully evaluable from step {question.evaluable_from}")

        verdicts = []
        raised_anywhere: set[str] = set()
        for check in question.checks:
            result = find_paths(graph, *check.between)
            explanation = explain(result, graph)
            selected = result.selected.edges if result.selected is not None else None
            raised = tuple(warning.code for warning in explanation.warnings)
            verdict = judge(check, selected, raised)
            verdicts.append(verdict)
            raised_anywhere.update(raised)
            check_statuses.append(verdict.status)

            print(f"    {_LABEL[verdict.status]}  {check.between[0]} to {check.between[1]}")
            if verdict.status == MATCH:
                continue
            wrong = [
                name
                for name, right in (("path", verdict.path_accepted), ("warnings", verdict.warnings_as_expected))
                if not right
            ]
            print(f"{'differs in':>19}  {' and '.join(wrong) if wrong else 'nothing'}")
            _expected(check)
            if selected is None:
                print(f"{'selected':>19}  no path")
            else:
                _pairs("selected", selected)
            print(f"{'':>19}  warnings: {_codes(raised)}; rule: {result.rule}")
            if check.prediction is not None:
                held = "CONFIRMED" if verdict.prediction_held else "REFUTED"
                _pairs("predicted", edges_of(check.prediction.path))
                print(f"{'':>19}  warnings: {_codes(check.prediction.warnings)}; prediction {held}")

        status = judge_question(question, eval_set.warning_categories, tuple(verdicts), frozenset(raised_anywhere))
        question_statuses.append(status)
        detail = ""
        if question.at_step_5 == PARTIAL:
            matched = sum(verdict.status == MATCH for verdict in verdicts)
            detail = (
                f"\n              partial check only: {matched} of {len(verdicts)} fixed pairs match,"
                " which says nothing of the question"
            )
        elif status == NOT_EVALUABLE:
            detail = " (no concrete pair of tables)"
        elif status == EXPECTED_FAILURE_CONFIRMED and eval_set.warning_categories[question.warning] == NO_MECHANISM:
            detail = f" (nothing raised {question.warning})"
        print(f"    question: {_LABEL[status]}{detail}")

    print(f"\n{'checks':<12}{len(check_statuses)}: {_tally(check_statuses)}")
    print(f"{'questions':<12}{len(question_statuses)}: {_tally(question_statuses)}")


if __name__ == "__main__":
    main()
