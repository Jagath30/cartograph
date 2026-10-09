"""The step 6 report: every question of the evaluation set put through
retrieval and the join tree, and judged (FR-37, DR-13, DR-16).

Printed after the step 5 report by `python -m app.show_eval --step6`, and
never instead of it. The two are shown side by side; their counts are never
added together.

For each question: what was retrieved, which tables became anchors and
which were set aside, the tree that was built and how each anchor was
attached, what it warns about, and the verdict of the step 6 judgement
(app.core.eval_step6), part by part. The pair checks of step 5 are read on
the path within the tree and shown as information; they decide nothing.

A REPORT, NOT A TEST: it exits 0 whatever it finds and changes nothing.

THE MARGIN AND THE FLOOR are not arguments. They are read from
eval/calibration.json, which was computed from the schema alone and
committed before any question was embedded. The report refuses to run if
that file, the stored snapshot and the live warehouse are not all the same
schema: scores from one schema judged with thresholds from another would
look like numbers and mean nothing.

No logic lives here: score (shell), locate and judge (core), print.
"""

import json
import statistics
import sys
import textwrap
import time
from pathlib import Path

from app.config import get_settings
from app.core.eval_set import MATCH, EvalSet, Question
from app.core.eval_step6 import AGREES, DISAGREES, Produced, TreeRoute, judge_step6
from app.core.explainer import route_codes
from app.core.join_tree import NO_ANCHORS
from app.core.locate import Located, locate
from app.core.path_finder import DEFAULT_MAX_JOINS
from app.core.retriever import Settings
from app.shell.embedder import Embedded, EmbeddingKeyMissing, MeteredEmbedder, make_embedder
from app.shell.semantic_index import SemanticIndex
from app.shell.snapshot_store import SnapshotStore, snapshot_hash
from app.shell.vector_cache import CachedEmbedder

EVAL = Path(__file__).resolve().parents[1] / "eval"
CALIBRATION = EVAL / "calibration.json"
CACHE = EVAL / ".cache"

_LABEL = {
    "match": "MATCH",
    "mismatch": "MISMATCH",
    "expected_failure_confirmed": "EXPECTED FAILURE CONFIRMED",
    "expected_failure_unexpectedly_passed": "EXPECTED FAILURE UNEXPECTEDLY PASSED",
    "not_evaluable": "NOT EVALUABLE AT STEP 5",
    AGREES: "AGREES",
    DISAGREES: "DISAGREES",
    "not_evaluable_at_step_6": "NOT EVALUABLE AT STEP 6",
}


class _NoKey:
    """Stands where the real embedder would when no key is configured. A
    run whose vectors are all cached never reaches it; one that needs a new
    vector gets the usual message."""

    def __init__(self, model: str, missing: EmbeddingKeyMissing) -> None:
        self.model = model
        self._missing = missing

    def embed(self, texts: list[str]) -> Embedded:
        raise self._missing


def produced(question: Question, located: Located) -> Produced:
    """What the step 6 judgement is handed: plain data, nothing computed
    here but the shape."""
    if located.declined:
        return Produced(True, frozenset(), frozenset(), frozenset(), ())
    tree = located.tree
    routes = []
    for check in question.checks:
        route = tree.route(*check.between)
        routes.append(TreeRoute(check.between, route.edges if route else None, route_codes(tree, *check.between)))
    return Produced(False, frozenset(located.tables), tree.edges, located.warning_codes, tuple(routes))


def _line(label: str, text: str) -> None:
    lines = textwrap.wrap(text, width=96, subsequent_indent="  ") or [""]
    print(f"    {label:<11}{lines[0]}")
    for line in lines[1:]:
        print(f"    {'':<11}{line}")


def _joins(edges) -> list[str]:
    return sorted(" and ".join(f"{start} = {end}" for start, end in sorted(join)) for join in edges)


def _expected_joins(question: Question) -> list[str]:
    lines = []
    for alternatives in question.joins:
        options = [" and ".join(f"{start} = {end}" for start, end in sorted(join.edges)) for join in alternatives]
        lines.append(options[0] if len(options) == 1 else "one of: " + "  |  ".join(options))
    return lines


def report(eval_set: EvalSet, snapshot, graph, step5: dict[int, str], arguments) -> None:
    settings = get_settings()
    if not CALIBRATION.exists():
        sys.exit("step 6: eval/calibration.json is missing. Run: python -m app.calibrate")
    calibration = json.loads(CALIBRATION.read_text())

    stored = SnapshotStore(settings.app_database_url).current()
    hashes = {"live warehouse": snapshot_hash(snapshot), "stored snapshot": stored.hash,
              "calibration": calibration["snapshot_sha256"]}  # fmt: skip
    if len(set(hashes.values())) != 1:
        described = "; ".join(f"{name} {value[:12]}" for name, value in hashes.items())
        sys.exit(
            f"step 6: these are not the same schema: {described}. Run python -m app.ingest_schema, "
            "then python -m app.calibrate."
        )
    if str(arguments.alpha) not in calibration["margin"]:
        sys.exit(
            f"step 6: no margin was computed for alpha {arguments.alpha}. The grid is "
            f"{', '.join(calibration['margin'])}; a margin is never made up for another value."
        )
    margin = calibration["margin"][str(arguments.alpha)]["value"]
    retrieval_settings = Settings(arguments.alpha, arguments.cut, arguments.cap, margin)

    try:
        paid = MeteredEmbedder(make_embedder(settings))
    except EmbeddingKeyMissing as missing:
        paid = MeteredEmbedder(_NoKey(calibration["embedding_model"], missing))
    index = SemanticIndex(settings.app_database_url, CachedEmbedder(paid, CACHE))

    print("\n" + "=" * 100)
    print("STEP 6: retrieval and the join tree, judged on the tree as a whole")
    print("=" * 100)
    print(f"{'settings':<12}alpha {arguments.alpha}; anchor cut {arguments.cut}, cap {arguments.cap}; "
          f"subgraph bound {arguments.bound}; routes of up to {DEFAULT_MAX_JOINS} joins")  # fmt: skip
    print(f"{'fixed':<12}margin {margin:.6f} (for this alpha). From eval/calibration.json, computed "
          f"{calibration['computed_on']}")  # fmt: skip
    print(f"{'':<12}from the schema alone, before any question was embedded. Not tuned.")
    print(f"{'floor':<12}WITHDRAWN by the owner's ruling after run 0 (it declined all sixteen). Nothing replaces it:")
    print(f"{'':<12}a question is declined only when its anchors cannot be connected (DD-10).")
    print(f"{'rivals':<12}CORRECTED by the owner's ruling after run 1: two tables joined by a foreign key, either")
    print(f"{'':<12}way round, are partners and are never set aside for one another. A \"partners\" line lists the")
    print(f"{'':<12}tables that reached the cut, that no term nominates, and that were kept for this reason alone.")
    print(f"{'':<12}REVISED at the fourth review stop: a table is set aside only for a winner that is still an")
    print(f"{'':<12}anchor after the cap; and a close call is shown as information (\"close call\"), never raised")
    print(f"{'':<12}as the warning anchor_ambiguity. Questions 8, 9 and 13 expect that warning and keep expecting it.")
    print(f"{'snapshot':<12}{stored.id}, sha256 {stored.hash}; embedded with {stored.embedding_model}")

    statuses: list[str] = []
    parts = {"decline": [], "tables": [], "joins": [], "warnings": []}
    anchor_counts: list[tuple[int, int]] = []
    all_found: list[bool] = []
    none_extra: list[bool] = []
    score_ms: list[float] = []
    core_ms: list[float] = []
    close_call_counts: list[int] = []
    surplus = 0
    raised_codes: list[str] = []

    for question in eval_set.questions:
        began = time.monotonic()
        scored = index.score_question(question.question)
        scored_at = time.monotonic()
        located = locate(
            question.question, scored.scores, scored.terms, graph, retrieval_settings,
            max_joins=DEFAULT_MAX_JOINS, subgraph_bound=arguments.bound,
        )  # fmt: skip
        located_at = time.monotonic()
        score_ms.append((scored_at - began) * 1000)
        core_ms.append((located_at - scored_at) * 1000)

        verdict = judge_step6(question, produced(question, located))
        statuses.append(verdict.status)
        retrieval = located.retrieval
        anchor_counts.append((question.id, len(retrieval.anchors)))

        wrong = [
            name
            for name, held in (
                ("decline", verdict.decline_as_expected), ("tables", verdict.tables_as_expected),
                ("joins", verdict.joins_as_expected), ("warnings", verdict.warnings_as_expected),
            )
            if held is False
        ]  # fmt: skip
        for name, held in (
            ("decline", verdict.decline_as_expected), ("tables", verdict.tables_as_expected),
            ("joins", verdict.joins_as_expected), ("warnings", verdict.warnings_as_expected),
        ):  # fmt: skip
            if held is not None:
                parts[name].append(held)

        print(f"\n{question.id:>2}  {question.question}")
        differs = f"  -- differs in: {', '.join(wrong)}" if wrong else ""
        print(f"    step 5: {_LABEL[step5[question.id]]}    |    step 6: {_LABEL[verdict.status]}{differs}")

        best = max(retrieval.candidates, key=lambda candidate: candidate.semantic_raw)
        _line("best raw", f"{retrieval.best_raw:.4f} ({best.element})  (information only)")
        _line("terms", "; ".join(f"{term.term.text} -> {term.chosen_table}" for term in retrieval.terms) or "none")
        _line("tables", ", ".join(
            f"{entry.table} {entry.score:.3f} (by {entry.best.split('.')[-1]})" for entry in retrieval.tables[:6]
        ))  # fmt: skip
        _line("columns", ", ".join(
            f"{c.element} {c.combined:.3f} [sem {c.semantic_raw:.3f}, key {c.keyword_raw:.3f}]"
            for c in [candidate for candidate in retrieval.candidates if candidate.column][:4]
        ))  # fmt: skip

        bound = retrieval.anchor_bound
        # Every pair that set a table aside. The winner is always an anchor.
        above_cut = {*retrieval.anchors, *bound.excluded_by_cap, *bound.set_aside_as_rivals}
        aside = "; ".join(
            f"{rival.rival} (\"{rival.term}\" chose {rival.chosen}: {rival.chosen_score:.3f} against "
            f"{rival.rival_score:.3f})"
            for rival in retrieval.rivals
            if rival.rival in bound.set_aside_as_rivals and rival.chosen in retrieval.anchors
        )
        nominated = {term.chosen_table for term in retrieval.terms}
        kept = "; ".join(
            f"{partner.rival} (joined to {partner.chosen}, which \"{partner.term}\" chose: "
            f"{partner.chosen_score:.3f} against {partner.rival_score:.3f})"
            for term in retrieval.terms
            for partner in term.partners
            if partner.chosen in above_cut
            and partner.rival in above_cut
            and partner.rival not in nominated
            and partner.rival not in bound.set_aside_as_rivals
        )
        _line("partners", kept or "none")
        _line("cap cut", ", ".join(bound.excluded_by_cap) or "none")

        tree = located.tree
        if located.declined:
            _line("DECLINED", located.explanation.reason)
        elif tree.decline_reason == NO_ANCHORS:
            _line("tree", "none: no table reached the anchor cut")
        else:
            for attachment, explained in zip(tree.attachments, located.explanation.attachments):
                if attachment.path is None:
                    _line("attached", f"0  {attachment.anchor}: the seed")
                    continue
                tied = f", {len(attachment.tied)} tied" if attachment.tied else ""
                evidence = ""
                if attachment.evidence:
                    (_, top), (_, second) = attachment.evidence[:2]
                    evidence = f"; evidence {top:.3f} against {second:.3f}, margin {attachment.margin:.3f}"
                _line("attached", f"{attachment.order}  {attachment.anchor} to {attachment.attached_to} "
                                  f"by {explained.path.id}  [{attachment.rule}{tied}{evidence}]")  # fmt: skip
            _line("tree", ", ".join(tree.tables))
            if tree.subgraph_bound.dropped_anchors or tree.subgraph_bound.excluded:
                _line("bound", f"dropped anchors: {', '.join(tree.subgraph_bound.dropped_anchors) or 'none'}; "
                               f"alternatives left out: {', '.join(tree.subgraph_bound.excluded) or 'none'}")  # fmt: skip

        if question.warning == "decline":
            _line("expected", "DECLINE: the warehouse cannot answer this")
        else:
            one_of = f" + one of {', '.join(question.tables_one_of)}" if question.tables_one_of else ""
            _line("expected", f"{', '.join(question.tables)}{one_of}")
            expected = set(question.tables) | set(question.tables_one_of)
            found = set(located.tables)
            missing = sorted(set(question.tables) - found)
            if question.tables_one_of and not found & set(question.tables_one_of):
                missing.append(f"one of {'/'.join(question.tables_one_of)}")
            extra = sorted(found - expected) + sorted(found & set(question.tables_one_of))[1:]
            all_found.append(not missing)
            none_extra.append(not extra)
            surplus += len(extra)
            _line("", f"missing: {', '.join(missing) or 'none'}; extra: {', '.join(extra) or 'none'}")

            if not located.declined:
                for position, join in enumerate(_joins(tree.edges)):
                    _line("joins" if position == 0 else "", join)
                if not tree.edges:
                    _line("joins", "none")
            for position, join in enumerate(_expected_joins(question)):
                _line("exp. joins" if position == 0 else "", join)

        raised = sorted(located.warning_codes)
        raised_codes += raised
        wanted = "not judged (see_note)" if question.warning == "see_note" else question.warning
        _line("warnings", f"{', '.join(raised) or 'none'}; expected: {wanted}")
        if not located.declined:
            for warning in located.explanation.warnings:
                _line("", f"{warning.code}: {warning.text}")
            close_call_counts.append(len(located.explanation.close_calls))
            for position, call in enumerate(located.explanation.close_calls):
                _line("close call" if position == 0 else "", call.text)

        if verdict.checks:
            _line("checks", "; ".join(
                f"{_LABEL[check_verdict.status]} {check.between[0]} to {check.between[1]}"
                for check, check_verdict in zip(question.checks, verdict.checks)
            ) + "  (information only)")  # fmt: skip

    count = len(statuses)
    print("\n" + "-" * 100)
    step5_matches = sum(status == MATCH for status in step5.values())
    print(f"{'step 5':<12}{count} questions: {step5_matches} match  (pairwise PathFinder, handed the expected tables; "
          "unchanged, see above)")  # fmt: skip
    print(f"{'step 6':<12}{count} questions: {statuses.count(AGREES)} agree, {statuses.count(DISAGREES)} disagree  "
          "(retrieval and the tree, judged whole)")  # fmt: skip
    print(f"{'':<12}These are different instruments. The two counts are not added and not compared as a score.")
    for name, label in (("decline", "declined or not, as expected"), ("tables", "tables as expected"),
                        ("joins", "joins as expected"), ("warnings", "warnings as expected")):  # fmt: skip
        print(f"{'part':<12}{label:<30}{sum(parts[name]):>2} of {len(parts[name])} judged")
    print(f"{'diagnostic':<12}every expected table in the tree: {sum(all_found)} of {len(all_found)}; "
          f"no table beyond the expected: {sum(none_extra)} of {len(none_extra)}")  # fmt: skip
    print(f"{'':<12}tables beyond the expected, over all questions: {surplus}")
    print(f"{'warnings':<12}raised, questions each: "
          + (", ".join(f"{code} {raised_codes.count(code)}" for code in sorted(set(raised_codes))) or "none"))  # fmt: skip
    print(f"{'close calls':<12}{sum(close_call_counts)} on {sum(count > 0 for count in close_call_counts)} questions "
          "(information, not a warning)")  # fmt: skip
    print(f"{'anchors':<12}per question: " + ", ".join(f"{number}:{anchors}" for number, anchors in anchor_counts))
    print(f"{'timing':<12}scoring (embedding lookups and both searches) median {statistics.median(score_ms):.0f} ms, "
          f"largest {max(score_ms):.0f} ms; retrieval and tree median {statistics.median(core_ms):.0f} ms, "
          f"largest {max(core_ms):.0f} ms")  # fmt: skip
    print(f"{'':<12}external embedding calls, not in the figures' budget (NFR-02) but in them here: "
          f"{paid.seconds * 1000:.0f} ms in all")  # fmt: skip
    print(f"{'cost':<12}embedded now: {paid.texts} texts, {paid.tokens} tokens, ${paid.cost_usd:.8f}; "
          "everything else came from the cache")  # fmt: skip
    print(f"{'note':<12}With {count} questions a difference of one is noise.")
