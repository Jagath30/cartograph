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

WHAT IS JUDGED: THE SELECTED TREE AS A WHOLE, not pair by pair. A question
AGREES only if every part that applies to it holds.

  decline   The question's warning is `decline` if and only if the system
            declined it. When either side says decline, nothing else is
            judged: there is no tree to look at.

  tables    The tables of the selected join tree, after expansion, are
            exactly the question's `tables` -- plus exactly one of its
            `tables_one_of`, where it has any. Not the anchors: a bridging
            table may score badly and arrive only through expansion
            (DD-10). Tables kept on tied alternatives do not count. One
            table too many disagrees just as one too few does.

  joins     The joins of the selected tree are exactly the question's
            `joins`: every entry of that field is used, by exactly one of
            its `one_of` alternatives where it has any, and the tree holds
            no join besides. `joins` is the set's own statement of the
            joins a correct answer uses.

  warnings  The warning codes raised about the answer as a whole are
            exactly the one the question expects: none at all for `none`,
            that one code and no other for the rest. Exactly, for the
            reason step 5 gives: "at least the expected warning" would let
            a wrong answer pass on a warning about something else.
            `see_note` (question 16) states no warning of its own and this
            part is not judged for it.

PAIR CHECKS INFORM; THEY DO NOT DECIDE. Each pair check of the question is
still put to step 5's own `judge`, unchanged, on the path between its two
tables within the selected tree, and the verdicts are reported. They were a
step 5 device for a PathFinder that saw two tables at a time. Read within
one tree they can contradict the set's own `joins`: question 14 accepts
either item key, and its two item checks can then never both hold. So a
question is decided by the four parts above and by nothing else. (The
owner's correction to ruling g, made at the first review stop, before any
retrieval existed.)

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
    # The joins of that tree, each one the column pairs of one foreign key.
    tree_joins: frozenset[Edges]
    # Every warning code raised about the answer as a whole.
    warnings: frozenset[str]
    # One for each pair check of the question. Reported, never decisive.
    routes: tuple[TreeRoute, ...]


@dataclass(frozen=True)
class Step6Verdict:
    status: Step6Status
    # Each part is True or False where it was judged and None where it was
    # not: after a decline on either side, or for `see_note`.
    decline_as_expected: bool | None = None
    tables_as_expected: bool | None = None
    joins_as_expected: bool | None = None
    warnings_as_expected: bool | None = None
    # Step 5's verdict on each pair check, in the question's order, judged
    # on the path within the tree. Information: it decides nothing.
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


def joins_as_expected(question: Question, tree_joins: frozenset[Edges]) -> bool:
    """Every entry of the question's `joins` is used by exactly one of its
    alternatives, and the tree holds no other join."""
    entries = [frozenset(frozenset(join.edges) for join in alternatives) for alternatives in question.joins]
    for number, entry in enumerate(entries):
        if any(entry & other for other in entries[number + 1 :]):
            # Then "which entry does this join answer" has two answers, and
            # the counting below would be wrong.
            raise ValueError(f"question {question.id}: the same join appears in two entries of `joins`")
    named = frozenset().union(*entries) if entries else frozenset()
    return tree_joins <= named and all(len(entry & tree_joins) == 1 for entry in entries)


def judge_step6(question: Question, produced: Produced) -> Step6Verdict:
    unknown = sorted(produced.warnings - STEP6_WARNINGS)
    if unknown:
        raise ValueError(f"question {question.id}: {unknown} are not warning codes; expected {sorted(STEP6_WARNINGS)}")
    if produced.declined and (produced.tree_tables or produced.tree_joins or produced.warnings or produced.routes):
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
    joins = joins_as_expected(question, produced.tree_joins)
    expected = expected_warnings(question)
    warnings = None if expected is None else produced.warnings == expected

    agreed = tables and joins and warnings is not False
    return Step6Verdict(
        AGREES if agreed else DISAGREES,
        decline_as_expected=True,
        tables_as_expected=tables,
        joins_as_expected=joins,
        warnings_as_expected=warnings,
        checks=tuple(verdicts),
    )
