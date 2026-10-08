"""The evaluation set: parsed, and judged against what the system did
(DR-13, DR-16, FR-37, SRS threat T-04).

Three things happen here, all pure (DD-01):

  parse_eval_set   YAML text -> an EvalSet value. Reading the file is the
                   caller's job; this takes the text it read.
  judge            one check of the set + the path the PathFinder selected
                   and the warnings raised -> a Verdict.
  judge_question   a question + the verdicts of its checks -> one status for
                   the question as a whole.

NOTHING HERE KNOWS WHAT THE RIGHT ANSWER IS. The expectations are data, in
backend/eval/questions.yaml, written before any of this existed. This module
compares; it never computes an expectation, and it imports no component it
is used to judge.

THE FIVE STATUSES. Four are verdicts on something that was run:

  match                               the selected path is one the set
                                      accepts and the warnings are exactly
                                      the ones it expects
  mismatch                            either differs
  expected_failure_confirmed          the set said in advance this would
                                      fail, and it failed
  expected_failure_unexpectedly_passed  it said so, and it passed

and one is not a verdict at all:

  not_evaluable                       nothing at this step can speak for the
                                      question. A question that is only
                                      partly checked is not evaluable either,
                                      however many of its checks match.

A failure is "expected" only where the set itself says so: a check marked
`expected_to_fail`, or a question whose warning has no mechanism yet. No
other disagreement is ever softened into one.

The parser is strict. A field it does not understand, a question without a
warning, a check whose path does not connect the two tables it is between:
each raises. A set that is quietly missing an expectation would report a
better score, and nothing downstream would say so.
"""

from dataclasses import dataclass
from typing import Literal

import yaml

Status = Literal[
    "match",
    "mismatch",
    "expected_failure_confirmed",
    "expected_failure_unexpectedly_passed",
    "not_evaluable",
]

MATCH = "match"
MISMATCH = "mismatch"
EXPECTED_FAILURE_CONFIRMED = "expected_failure_confirmed"
EXPECTED_FAILURE_UNEXPECTEDLY_PASSED = "expected_failure_unexpectedly_passed"
NOT_EVALUABLE = "not_evaluable"

# What a warning category can be: something the system can raise today, or
# something no component produces yet.
EXISTS = "exists"
NO_MECHANISM = "no_mechanism"

CHECKABLE = "checkable"
PARTIAL = "partial"

# The codes a path explanation can carry (app.core.explainer.WarningCode).
# Repeated here, not imported: this module judges that component.
PAIR_WARNINGS = {"arbitrary_choice", "preference_not_applied", "many_to_many", "no_path"}

_TOP = {"provenance", "derivation", "warning_categories", "questions"}
_QUESTION = {"id", "question", "tables", "warning", "evaluable_from", "at_step_5", "joins", "checks"}
_QUESTION_OPTIONAL = {"tables_one_of", "note", "ambiguity_seen"}
_CHECK = {"between", "accept", "warnings"}
_CHECK_OPTIONAL = {"expected_to_fail", "prediction"}
_STEPS = {5, 6, 7}
_AT_STEP_5 = {CHECKABLE, PARTIAL, NOT_EVALUABLE}

Edges = frozenset[tuple[str, str]]


@dataclass(frozen=True)
class ExpectedJoin:
    """One foreign key a correct answer joins on: `from` the referencing
    side, `to` the referenced side, as in the overlay."""

    from_table: str
    from_columns: tuple[str, ...]
    to_table: str
    to_columns: tuple[str, ...]

    @property
    def edges(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (f"{self.from_table}.{start}", f"{self.to_table}.{end}")
            for start, end in zip(self.from_columns, self.to_columns)
        )


ExpectedPath = tuple[ExpectedJoin, ...]


@dataclass(frozen=True)
class Prediction:
    """What the set's authors said, in advance, the system would do where
    they expected it to be wrong."""

    path: ExpectedPath
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class Check:
    between: tuple[str, str]
    # The selected path must be one of these.
    accept: tuple[ExpectedPath, ...]
    # The warning codes raised must be exactly these.
    warnings: tuple[str, ...]
    expected_to_fail: bool = False
    prediction: Prediction | None = None


@dataclass(frozen=True)
class Question:
    id: int
    question: str
    tables: tuple[str, ...]
    tables_one_of: tuple[str, ...]
    warning: str
    note: str | None
    evaluable_from: int
    at_step_5: str
    # Each entry is the alternatives for one join: a single join where the
    # question fixes it, several where a correct answer uses exactly one.
    joins: tuple[tuple[ExpectedJoin, ...], ...]
    checks: tuple[Check, ...]
    ambiguity_seen: str | None


@dataclass(frozen=True)
class EvalSet:
    provenance: str
    derivation: str
    warning_categories: dict[str, str]
    questions: tuple[Question, ...]

    @property
    def joins_named(self) -> tuple[ExpectedJoin, ...]:
        """Every join the set names anywhere: in a question's joins, in a
        path a check accepts, in a prediction."""
        named: list[ExpectedJoin] = []
        for question in self.questions:
            named += [join for alternatives in question.joins for join in alternatives]
            for check in question.checks:
                named += [join for path in check.accept for join in path]
                if check.prediction is not None:
                    named += check.prediction.path
        return tuple(named)

    @property
    def tables_named(self) -> frozenset[str]:
        named: set[str] = set()
        for question in self.questions:
            named.update(question.tables, question.tables_one_of)
            for check in question.checks:
                named.update(check.between)
        for join in self.joins_named:
            named.update((join.from_table, join.to_table))
        return frozenset(named)

    @property
    def columns_named(self) -> frozenset[tuple[str, str]]:
        """(table, column) for every column in every join the set names."""
        return frozenset(
            (table, column)
            for join in self.joins_named
            for table, columns in ((join.from_table, join.from_columns), (join.to_table, join.to_columns))
            for column in columns
        )


@dataclass(frozen=True)
class Verdict:
    status: Status
    path_accepted: bool
    warnings_as_expected: bool
    # Only for a check that carries a prediction: did the system do exactly
    # what was predicted?
    prediction_held: bool | None = None


def edges_of(path: ExpectedPath) -> Edges:
    return frozenset(edge for join in path for edge in join.edges)


def judge(check: Check, selected: Edges | None, raised: tuple[str, ...]) -> Verdict:
    """`selected` is the column pairs of the path the system chose between
    the check's two tables, each (referencing column, referenced column), or
    None when it chose nothing. `raised` is the warning codes it gave."""
    path_accepted = selected is not None and any(selected == edges_of(path) for path in check.accept)
    warnings_as_expected = sorted(raised) == sorted(check.warnings)
    passed = path_accepted and warnings_as_expected

    prediction_held = None
    if check.prediction is not None:
        prediction_held = selected == edges_of(check.prediction.path) and sorted(raised) == sorted(
            check.prediction.warnings
        )

    if check.expected_to_fail:
        status = EXPECTED_FAILURE_UNEXPECTEDLY_PASSED if passed else EXPECTED_FAILURE_CONFIRMED
    else:
        status = MATCH if passed else MISMATCH
    return Verdict(status, path_accepted, warnings_as_expected, prediction_held)


def judge_question(
    question: Question, categories: dict[str, str], verdicts: tuple[Verdict, ...], raised: frozenset[str]
) -> Status:
    """One status for the whole question, from its checks' verdicts and
    every warning code raised across them.

    The worst news wins: a mismatch anywhere is a mismatch, whatever else
    matched. A question whose warning has no mechanism yet is an expected
    failure when everything else holds -- and has unexpectedly passed only
    if something really did raise that warning.
    """
    if question.at_step_5 != CHECKABLE:
        return NOT_EVALUABLE

    statuses = {verdict.status for verdict in verdicts}
    if MISMATCH in statuses:
        return MISMATCH

    if categories[question.warning] == NO_MECHANISM:
        statuses.add(EXPECTED_FAILURE_UNEXPECTEDLY_PASSED if question.warning in raised else EXPECTED_FAILURE_CONFIRMED)

    for status in (EXPECTED_FAILURE_UNEXPECTEDLY_PASSED, EXPECTED_FAILURE_CONFIRMED):
        if status in statuses:
            return status
    return MATCH


def parse_eval_set(text: str) -> EvalSet:
    document = yaml.safe_load(text)
    if not isinstance(document, dict):
        raise ValueError("evaluation set: the file must be a mapping")
    _exactly(document, _TOP, set(), "evaluation set")

    provenance = _text(document["provenance"], "provenance")
    derivation = _text(document["derivation"], "derivation")

    categories = document["warning_categories"]
    if not isinstance(categories, dict) or not categories:
        raise ValueError("evaluation set: warning_categories must be a mapping")
    for name, mechanism in categories.items():
        if not isinstance(name, str) or mechanism not in (EXISTS, NO_MECHANISM):
            raise ValueError(
                f"evaluation set: warning category {name!r} must be `{EXISTS}` or `{NO_MECHANISM}`, got {mechanism!r}"
            )

    entries = document["questions"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("evaluation set: questions must be a list")
    questions = tuple(_question(entry, categories) for entry in entries)

    ids = [question.id for question in questions]
    if ids != list(range(1, len(ids) + 1)):
        raise ValueError(f"evaluation set: question ids must run 1, 2, 3, ... in order, got {ids}")
    return EvalSet(provenance, derivation, dict(categories), questions)


def _question(entry: object, categories: dict[str, str]) -> Question:
    if not isinstance(entry, dict):
        raise ValueError(f"evaluation set: a question must be a mapping, got {entry!r}")
    where = f"question {entry.get('id')!r}"
    _exactly(entry, _QUESTION, _QUESTION_OPTIONAL, where)

    if not isinstance(entry["id"], int) or isinstance(entry["id"], bool):
        raise ValueError(f"evaluation set: {where}: id must be a whole number")
    if entry["warning"] not in categories:
        raise ValueError(
            f"evaluation set: {where}: warning {entry['warning']!r} is not one of {sorted(categories)}"
        )
    if entry["evaluable_from"] not in _STEPS:
        raise ValueError(f"evaluation set: {where}: evaluable_from must be one of {sorted(_STEPS)}")
    if entry["at_step_5"] not in _AT_STEP_5:
        raise ValueError(f"evaluation set: {where}: at_step_5 must be one of {sorted(_AT_STEP_5)}")

    joins = entry["joins"]
    if not isinstance(joins, list):
        raise ValueError(f"evaluation set: {where}: joins must be a list")
    checks = entry["checks"]
    if not isinstance(checks, list):
        raise ValueError(f"evaluation set: {where}: checks must be a list")

    question = Question(
        id=entry["id"],
        question=_text(entry["question"], f"{where}: question"),
        tables=_names(entry["tables"], f"{where}: tables"),
        tables_one_of=_names(entry.get("tables_one_of", []), f"{where}: tables_one_of"),
        warning=entry["warning"],
        note=_text(entry["note"], f"{where}: note") if "note" in entry else None,
        evaluable_from=entry["evaluable_from"],
        at_step_5=entry["at_step_5"],
        joins=tuple(_alternatives(join, where) for join in joins),
        checks=tuple(_check(check, where) for check in checks),
        ambiguity_seen=_text(entry["ambiguity_seen"], f"{where}: ambiguity_seen") if "ambiguity_seen" in entry else None,
    )

    # A question step 5 cannot evaluate has nothing to run, and one it can
    # has something. Either contradiction would misreport the question.
    if (question.at_step_5 == NOT_EVALUABLE) != (not question.checks):
        raise ValueError(f"evaluation set: {where}: at_step_5 is {question.at_step_5} but it has {len(checks)} checks")
    if question.at_step_5 == CHECKABLE and question.evaluable_from == 5 and categories[question.warning] != EXISTS:
        raise ValueError(f"evaluation set: {where}: evaluable from step 5, yet nothing can raise {question.warning}")
    pairs = [tuple(sorted(check.between)) for check in question.checks]
    repeated = sorted({pair for pair in pairs if pairs.count(pair) > 1})
    if repeated:
        raise ValueError(f"evaluation set: {where}: more than one check between {repeated}")
    return question


def _check(entry: object, where: str) -> Check:
    if not isinstance(entry, dict):
        raise ValueError(f"evaluation set: {where}: a check must be a mapping, got {entry!r}")
    _exactly(entry, _CHECK, _CHECK_OPTIONAL, f"{where}: check")

    between = _names(entry["between"], f"{where}: between")
    if len(between) != 2 or between[0] == between[1]:
        raise ValueError(f"evaluation set: {where}: `between` must name two different tables, got {between}")
    where = f"{where}: check between {between[0]} and {between[1]}"

    accept = entry["accept"]
    if not isinstance(accept, list) or not accept:
        raise ValueError(f"evaluation set: {where}: `accept` must list at least one path")
    paths = tuple(_path(path, between, where) for path in accept)

    expected_to_fail = entry.get("expected_to_fail", False)
    if not isinstance(expected_to_fail, bool):
        raise ValueError(f"evaluation set: {where}: expected_to_fail must be true or false")

    prediction = None
    if "prediction" in entry:
        predicted = entry["prediction"]
        if not isinstance(predicted, dict) or set(predicted) != {"path", "warnings"}:
            raise ValueError(f"evaluation set: {where}: a prediction needs exactly `path` and `warnings`")
        prediction = Prediction(
            _path(predicted["path"], between, f"{where}: prediction"),
            _warnings(predicted["warnings"], f"{where}: prediction"),
        )
    # A prediction is what makes an expected failure checkable; one without
    # the other is either an excuse or a stray.
    if expected_to_fail != (prediction is not None):
        raise ValueError(f"evaluation set: {where}: expected_to_fail and prediction go together")

    return Check(between, paths, _warnings(entry["warnings"], where), expected_to_fail, prediction)


def _path(entry: object, between: tuple[str, ...], where: str) -> ExpectedPath:
    if not isinstance(entry, list) or not entry:
        raise ValueError(f"evaluation set: {where}: a path must be a list of joins, got {entry!r}")
    path = tuple(_join(join, where) for join in entry)

    # A route from one table to the other touches each of them once and
    # every table in between twice.
    touched = [table for join in path for table in (join.from_table, join.to_table)]
    ends = sorted(table for table in set(touched) if touched.count(table) == 1)
    if ends != sorted(between) or any(touched.count(table) > 2 for table in touched):
        raise ValueError(f"evaluation set: {where}: the path does not run from {between[0]} to {between[1]}")
    return path


def _alternatives(entry: object, where: str) -> tuple[ExpectedJoin, ...]:
    if isinstance(entry, dict) and set(entry) == {"one_of"}:
        choices = entry["one_of"]
        if not isinstance(choices, list) or len(choices) < 2:
            raise ValueError(f"evaluation set: {where}: `one_of` must list at least two joins")
        return tuple(_join(choice, where) for choice in choices)
    return (_join(entry, where),)


def _join(entry: object, where: str) -> ExpectedJoin:
    if not isinstance(entry, dict) or set(entry) != {"from", "to"}:
        raise ValueError(f"evaluation set: {where}: a join needs exactly `from` and `to`, got {entry!r}")

    sides = []
    for side in ("from", "to"):
        value = entry[side]
        names = [value] if isinstance(value, str) else value
        if not isinstance(names, list) or not names:
            raise ValueError(f"evaluation set: {where}: `{side}` must be table.column or a list of them, got {value!r}")
        tables, columns = set(), []
        for name in names:
            parts = name.split(".") if isinstance(name, str) else []
            if len(parts) != 2 or not all(parts):
                raise ValueError(f"evaluation set: {where}: `{side}` must be table.column, got {name!r}")
            tables.add(parts[0])
            columns.append(parts[1])
        if len(tables) != 1 or len(set(columns)) != len(columns):
            raise ValueError(
                f"evaluation set: {where}: the columns of `{side}` must be different columns of one table, got {value!r}"
            )
        sides.append((tables.pop(), tuple(columns)))

    (from_table, from_columns), (to_table, to_columns) = sides
    if len(from_columns) != len(to_columns):
        raise ValueError(f"evaluation set: {where}: `from` and `to` must list the same number of columns, got {entry!r}")
    return ExpectedJoin(from_table, from_columns, to_table, to_columns)


def _warnings(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not set(value) <= PAIR_WARNINGS or len(set(value)) != len(value):
        raise ValueError(
            f"evaluation set: {where}: warnings must be a list drawn from {sorted(PAIR_WARNINGS)}, got {value!r}"
        )
    return tuple(value)


def _names(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(name, str) and name for name in value):
        raise ValueError(f"evaluation set: {where} must be a list of names, got {value!r}")
    if len(set(value)) != len(value):
        raise ValueError(f"evaluation set: {where} names something twice: {value!r}")
    return tuple(value)


def _text(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"evaluation set: {where} must be text, got {value!r}")
    return " ".join(value.split())


def _exactly(mapping: dict, required: set[str], optional: set[str], where: str) -> None:
    missing = sorted(required - set(mapping))
    unknown = sorted(str(key) for key in mapping if key not in required | optional)
    if missing or unknown:
        raise ValueError(f"evaluation set: {where}: missing {missing}, not understood {unknown}")
