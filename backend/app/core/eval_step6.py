"""The step 6 judgement: what retrieval produced for a question, judged
against the evaluation set (DR-13, DR-16, FR-37, SRS threat T-04).

AN ADDITION, NOT A CHANGE. The set, backend/eval/questions.yaml, is frozen,
and so is how step 5 judges it: `judge` and `judge_question` in
app.core.eval_set. Neither is edited here. This module adds a second
judgement beside the first, for what step 5 could not see: which tables
were retrieved, what the answer as a whole was warned about, and whether a
question was declined.

WRITTEN BEFORE THERE WAS ANYTHING TO JUDGE. This file was committed before
any retrieval code existed, so the rule could not be shaped by a result.

Pure (DD-01): a question of the set and a `Produced` value in, a verdict
out. It imports no component it is used to judge. What the system did
arrives as plain data, and how that data is obtained is the runner's
business.

WHAT IS JUDGED, in four parts. A question AGREES only if every part that
applies to it holds.

  decline   The question's warning is `decline` if and only if the system
            declined it. When either side says decline, nothing else is
            judged: there is no tree to look at.

  tables    The tables of the selected join tree, after expansion, are
            exactly the question's `tables` -- plus exactly one of its
            `tables_one_of`, where it has any. Not the anchors: a bridging
            table may score badly and arrive only through expansion
            (DD-10). Tables kept on tied alternatives do not count. One
            table too many disagrees just as one too few does.

  warnings  The warning codes raised about the answer as a whole are
            exactly the one the question expects: none at all for `none`,
            that one code and no other for the rest. Exactly, for the
            reason step 5 gives: "at least the expected warning" would let
            a wrong answer pass on a warning about something else.
            `see_note` (question 16) states no warning of its own; its
            expectation lives in its checks and this part is not judged.

  checks    Every pair check of the question, judged by step 5's own
            `judge`, unchanged -- but on the path between the two tables
            WITHIN THE SELECTED TREE, with the warnings the system raises
            about that path. A table the tree does not hold has no path.

A PREDICTED FAILURE IS STILL A FAILURE HERE. A check the set marks
`expected_to_fail` keeps the status step 5's `judge` gives it, and that
status is reported. But the question agrees only if the check holds in
substance: the path is one the set accepts and the warnings are the ones it
expects. A wrong path that was predicted is a wrong path.

THE TWO STATUSES ARE NOT STEP 5's. `agrees` and `disagrees` say whether the
whole of what retrieval produced is what the set expects. They are never
called match and mismatch, and never added to step 5's counts: the two
reports are shown side by side.
"""

from dataclasses import dataclass
from typing import Literal

from app.core.eval_set import PAIR_WARNINGS, Edges, Question, Verdict, judge

Step6Status = Literal["agrees", "disagrees", "not_evaluable_at_step_6"]

AGREES = "agrees"
DISAGREES = "disagrees"
NOT_EVALUABLE_AT_STEP_6 = "not_evaluable_at_step_6"

DECLINE = "decline"
NONE = "none"
SEE_NOTE = "see_note"

# Every code the system can raise about an answer at step 6: the four a
# path explanation carries, and the two retrieval adds. Repeated here, not
# imported from the components that raise them: this module judges those.
STEP6_WARNINGS = PAIR_WARNINGS | {"anchor_ambiguity", "multi_anchor"}


@dataclass(frozen=True)
class TreeRoute:
    """The system's path between two tables within its selected tree."""

    between: tuple[str, str]
    # The column pairs of that path, each (referencing column, referenced
    # column). None when the tree does not hold both tables.
    selected: Edges | None
    # The warning codes the system raises about that path.
    raised: tuple[str, ...]


@dataclass(frozen=True)
class Produced:
    """What retrieval and expansion produced for one question."""

    declined: bool
    # The tables of the selected join tree after expansion.
    tree_tables: frozenset[str]
    # Every warning code raised about the answer as a whole.
    warnings: frozenset[str]
    # One for each pair check of the question.
    routes: tuple[TreeRoute, ...]


@dataclass(frozen=True)
class Step6Verdict:
    status: Step6Status
    # Each part is True or False where it was judged and None where it was
    # not: after a decline on either side, or for `see_note`.
    decline_as_expected: bool | None = None
    tables_as_expected: bool | None = None
    warnings_as_expected: bool | None = None
    checks_hold: bool | None = None
    # Step 5's verdict on each pair check, in the question's order, judged
    # on the path within the tree.
    checks: tuple[Verdict, ...] = ()


def expected_warnings(question: Question) -> frozenset[str] | None:
    """The codes a correct answer to this question raises, or None where the
    question states none of its own."""
    if question.warning == SEE_NOTE:
        return None
    if question.warning in (NONE, DECLINE):
        return frozenset()
    return frozenset({question.warning})


def tables_as_expected(question: Question, tree_tables: frozenset[str]) -> bool:
    required = frozenset(question.tables)
    extra = tree_tables - required
    if not required <= tree_tables:
        return False
    if not question.tables_one_of:
        return not extra
    return len(extra) == 1 and extra <= frozenset(question.tables_one_of)


def judge_step6(question: Question, produced: Produced) -> Step6Verdict:
    unknown = sorted(produced.warnings - STEP6_WARNINGS)
    if unknown:
        raise ValueError(f"question {question.id}: {unknown} are not warning codes; expected {sorted(STEP6_WARNINGS)}")
    if produced.declined and (produced.tree_tables or produced.warnings or produced.routes):
        raise ValueError(f"question {question.id}: declined, yet a tree, a warning or a route was produced")

    if question.evaluable_from > 6:
        return Step6Verdict(NOT_EVALUABLE_AT_STEP_6)

    expects_decline = question.warning == DECLINE
    if expects_decline or produced.declined:
        agreed = expects_decline and produced.declined
        return Step6Verdict(AGREES if agreed else DISAGREES, decline_as_expected=agreed)

    routes = {tuple(sorted(route.between)): route for route in produced.routes}
    verdicts = []
    for check in question.checks:
        pair = tuple(sorted(check.between))
        if pair not in routes:
            raise ValueError(f"question {question.id}: no route was reported between {pair[0]} and {pair[1]}")
        verdicts.append(judge(check, routes[pair].selected, routes[pair].raised))

    tables = tables_as_expected(question, produced.tree_tables)
    expected = expected_warnings(question)
    warnings = None if expected is None else produced.warnings == expected
    checks_hold = all(verdict.path_accepted and verdict.warnings_as_expected for verdict in verdicts)

    agreed = tables and warnings is not False and checks_hold
    return Step6Verdict(
        AGREES if agreed else DISAGREES,
        decline_as_expected=True,
        tables_as_expected=tables,
        warnings_as_expected=warnings,
        checks_hold=checks_hold,
        checks=tuple(verdicts),
    )
