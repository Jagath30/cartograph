"""The step 10 judgement: the SQL each mode generated, measured against
the evaluation sets (FR-37, FR-38, DD-20, DR-13, DR-16).

AN ADDITION, NOT A CHANGE. The sets, `eval_set.py`, `eval_step6.py`,
`heldout.py` and the step 7 instrument (`sql_reading.py`, `conformance.py`)
are frozen and imported, never edited. This module only decides, for one
answer, what the frozen instrument's readings mean against one question's
expectation. Written and tested on hand-written SQL before any question
of either set was put to a model in this step.

Pure (DD-01): an expectation, an answer and the schema in; a score out.

THE TWO MEASURES, IN BOTH MODES, AS THE OWNER RULED AT STOP 1.

  tables  The tables the SQL reads, as the frozen `extract_joins` lists
          them (in FROM, in a CTE, in a subquery of WHERE or SELECT), are
          exactly the question's `tables`, plus exactly one of
          `tables_one_of` where it has any. The rule is step 6's own
          `tables_as_expected`, applied to the SQL's tables in place of a
          tree's. Not measured only where the reader could not resolve
          every table reference: an unread source, or text that is not
          one SELECT.

  path    The joins the SQL performs, read by the frozen `extract_joins`,
          are compared by the frozen `compare` with the question's
          `joins`. Where an entry has `one_of` alternatives, every choice
          of one alternative per entry is compared; the path is correct
          when any choice CONFORMS: every expected join present, nothing
          foreign, no cross join, no half-joined key. Otherwise it is not
          measured when any choice is `not_checked` (it leaves the path
          denominator), and wrong when every choice is `incomplete` or
          `diverged`. `diverged` wins over `not_checked` inside `compare`,
          so a known wrong join is counted, never dropped.

  Both are judged on every answer that produced validated SQL: an
  answer, and an answer whose SQL then failed at execution (a timeout or
  any database error). A timeout depends on the machine and the scale
  factor, not on the method, and DD-20 measures the SQL (the owner's
  ruling 1 at the review of stop 2); execution outcomes are reported
  beside the score. The rest is decided before any SQL is read:

    a question expected to be declined (Q15, H9)
        correct on both when the answer is a decline, wrong on both
        otherwise
    any other question declined                       wrong on both
    validation failed after retries, model failed
      (malformed after retries, cut off)              wrong on both:
                                                      no SQL was accepted

  A transport or provider failure is not the method's behaviour and never
  reaches this module: the runner retries the call and voids the run.

QUESTION 14 IS DELETED FROM SCORING (item 54; the owner's ruling, 16
October 2026). It names no sales channel, yet its expectation assumes the
store channel, so no answer is determined by the question. It is still
run and its raw results are reported apart (DR-16: deleted, and the
deletion stated).

ITEM 66 is reported, never folded in. Item 66 expected two summaries
joined on one dimension attribute (`item.i_category = item.i_category`
across two CTEs) to read as diverged. Tested here, the frozen comparison
reads that as CONFORMS: a column set equal to itself is implied by any
path. So what is marked is wider and says less: an ATTRIBUTE JOIN, any
join condition between two columns neither of which is a key, whatever
the verdict. It is counted beside the score, never in it.

RETRIEVAL RECALL AND PRECISION, retrieval mode only, a diagnostic outside
the headline (DD-20): the tables shown to the model against the expected
tables, the `tables_one_of` counting as one slot.
"""

from dataclasses import dataclass
from typing import Literal

from app.core.conformance import Conformance, Edge, Extraction, compare, extract_joins
from app.core.eval_set import ExpectedJoin, Question
from app.core.eval_step6 import tables_as_expected
from app.core.heldout import HeldOutQuestion
from app.core.sql_reading import Schema

Verdict = Literal["correct", "wrong", "not_measured"]
CORRECT: Verdict = "correct"
WRONG: Verdict = "wrong"
NOT_MEASURED: Verdict = "not_measured"

ORIGINAL = "original"
HELDOUT = "heldout"

# (set, question id) -> why it is not scored. Pinned by a test.
EXCLUDED: dict[tuple[str, str], str] = {
    (ORIGINAL, "14"): (
        "deleted from scoring by the owner's ruling of 16 October 2026 (item 54, DR-16): the question names no "
        "sales channel, yet its expectation assumes the store channel, so no answer is determined by the question"
    ),
}

# The orchestrator's outcomes (IR-05), repeated here, not imported: this
# module judges what the orchestrator produced.
ANSWERED = "answered"
NOT_ANSWERABLE = "not_answerable"
EXECUTION_FAILED = "execution_failed"
# The failures that leave no accepted SQL to read.
NO_SQL = ("validation_failed", "model_failed")

@dataclass(frozen=True)
class Expectation:
    """One question of either set, as step 10 needs it."""

    set: str
    id: str
    question: str
    tables: tuple[str, ...]
    tables_one_of: tuple[str, ...]
    # Each entry: the alternatives for one join, of which exactly one is used.
    joins: tuple[tuple[ExpectedJoin, ...], ...]
    decline: bool

    @property
    def excluded(self) -> str | None:
        return EXCLUDED.get((self.set, self.id))


def from_original(question: Question) -> Expectation:
    return Expectation(
        ORIGINAL, str(question.id), question.question, question.tables, question.tables_one_of, question.joins,
        question.warning == "decline",
    )  # fmt: skip


def from_heldout(question: HeldOutQuestion) -> Expectation:
    return Expectation(
        HELDOUT, question.id, question.question, question.tables, (), tuple((join,) for join in question.joins),
        question.decline,
    )  # fmt: skip


@dataclass(frozen=True)
class Answer:
    """What one mode produced for one question."""

    # The orchestrator's outcome.
    outcome: str
    # The validated SQL, where there is one: answered, or failed at execution.
    sql: str | None = None


@dataclass(frozen=True)
class Choice:
    """One choice of an alternative per `joins` entry, and what the frozen
    comparison said of it."""

    edges: tuple[Edge, ...]
    conformance: Conformance


@dataclass(frozen=True)
class Score:
    tables: Verdict
    path: Verdict
    # In fixed words: how the verdict was reached.
    why: str
    # Every table the SQL reads, and why some could not be resolved.
    sql_tables: tuple[str, ...] = ()
    tables_unresolved: tuple[str, ...] = ()
    # Every choice of alternatives, compared. Empty when no SQL was read.
    choices: tuple[Choice, ...] = ()
    # Item 66, as found: join conditions between two columns that are not keys.
    attribute_joins: tuple[str, ...] = ()


# The frozen instrument's reasons that mean a table reference was not
# resolved: an unread source in some FROM, or text that is not one SELECT.
UNRESOLVED_TABLES = ("not_parsed", "not_one_statement", "not_a_select", "unread_source")


def referenced_tables(extraction: Extraction) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Every table the statement reads, as the frozen extractor lists them,
    and the reasons some table reference could not be resolved (none when
    all were)."""
    unresolved = sorted({item.reason for item in extraction.unchecked if item.reason in UNRESOLVED_TABLES})
    return tuple(sorted(extraction.tables)), tuple(unresolved)


def expected_edge(join: ExpectedJoin) -> Edge:
    return tuple(
        ((join.from_table, start), (join.to_table, end)) for start, end in zip(join.from_columns, join.to_columns)
    )


def choices_of(joins: tuple[tuple[ExpectedJoin, ...], ...]) -> tuple[tuple[Edge, ...], ...]:
    """Every way of taking one alternative from each entry, in order."""
    made: list[tuple[Edge, ...]] = [()]
    for alternatives in joins:
        made = [chosen + (expected_edge(join),) for chosen in made for join in alternatives]
    return tuple(made)


def path_verdict(choices: tuple[Choice, ...]) -> Verdict:
    outcomes = [choice.conformance.outcome for choice in choices]
    if "conforms" in outcomes:
        return CORRECT
    if "not_checked" in outcomes:
        return NOT_MEASURED
    return WRONG


def key_columns(snapshot) -> frozenset[tuple[str, str]]:
    """Every (table, column) in a primary key or on either side of a
    foreign key of a SchemaSnapshot."""
    columns: set[tuple[str, str]] = set()
    for key in snapshot.primary_keys:
        columns.update((key.table, column) for column in key.columns)
    for key in snapshot.foreign_keys:
        columns.update((key.from_table, column) for column in key.from_columns)
        columns.update((key.to_table, column) for column in key.to_columns)
    return frozenset(columns)


def attribute_joins(extraction: Extraction, keys: frozenset[tuple[str, str]]) -> tuple[str, ...]:
    """Item 66, as found: every join condition between two columns neither
    of which is a key, written `table.column = table.column`."""
    return tuple(
        f"{e.left[0]}.{e.left[1]} = {e.right[0]}.{e.right[1]}"
        for e in extraction.equalities
        if e.left not in keys and e.right not in keys
    )


def score(expectation: Expectation, answer: Answer, schema: Schema, keys: frozenset[tuple[str, str]]) -> Score:
    """`keys` is every (table, column) that is part of a primary or foreign
    key of the schema: item 66's attribute is a column that is none."""
    declined = answer.outcome == NOT_ANSWERABLE
    if expectation.decline:
        if declined:
            return Score(CORRECT, CORRECT, "declined as expected")
        return Score(WRONG, WRONG, "not declined" if answer.outcome in (ANSWERED, EXECUTION_FAILED) else answer.outcome)
    if declined:
        return Score(WRONG, WRONG, "declined")
    if answer.outcome in NO_SQL:
        return Score(WRONG, WRONG, answer.outcome)
    if answer.outcome not in (ANSWERED, EXECUTION_FAILED) or answer.sql is None:
        raise ValueError(f"question {expectation.id}: an outcome of {answer.outcome!r} with sql {answer.sql!r}")

    extraction: Extraction = extract_joins(answer.sql, schema)
    tables, unresolved = referenced_tables(extraction)
    if unresolved:
        tables_verdict: Verdict = NOT_MEASURED
    else:
        tables_verdict = CORRECT if tables_as_expected(expectation, frozenset(tables)) else WRONG

    choices = tuple(Choice(edges, compare(extraction, edges)) for edges in choices_of(expectation.joins))
    return Score(
        tables_verdict, path_verdict(choices), "read", tables, unresolved, choices, attribute_joins(extraction, keys)
    )


@dataclass(frozen=True)
class Recall:
    """Retrieval mode only: the tables shown to the model against those
    expected. `tables_one_of` is one slot, filled by any one of them; the
    others shown count as surplus."""

    matched: int
    expected: int
    shown: int

    @property
    def recall(self) -> float:
        return self.matched / self.expected

    @property
    def precision(self) -> float | None:
        return self.matched / self.shown if self.shown else None


def retrieval_recall(expectation: Expectation, shown: tuple[str, ...]) -> Recall | None:
    """None for a question expected to be declined: it expects no table."""
    if expectation.decline:
        return None
    seen = set(shown)
    one_of = set(expectation.tables_one_of)
    matched = len(set(expectation.tables) & seen) + (1 if seen & one_of else 0)
    return Recall(matched, len(expectation.tables) + (1 if one_of else 0), len(seen))


# --------------------------------------------------------------------------
# Runs combined (the owner's plan: the mean, the range, and each question's
# stability over a number of runs fixed in advance).
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Tally:
    """One measure, one mode, one set, one run."""

    correct: int
    measured: int
    not_measured: int

    @property
    def fraction(self) -> float | None:
        return self.correct / self.measured if self.measured else None


def tally(verdicts: list[Verdict]) -> Tally:
    unknown = [verdict for verdict in verdicts if verdict not in (CORRECT, WRONG, NOT_MEASURED)]
    if unknown:
        raise ValueError(f"{unknown} are not verdicts")
    measured = [verdict for verdict in verdicts if verdict != NOT_MEASURED]
    return Tally(measured.count(CORRECT), len(measured), len(verdicts) - len(measured))


@dataclass(frozen=True)
class Combined:
    runs: int
    mean: float | None
    low: float | None
    high: float | None


def combine(tallies: list[Tally]) -> Combined:
    """The mean of the runs' fractions, and their range. A run in which
    nothing was measured has no fraction and takes no part."""
    fractions = [t.fraction for t in tallies if t.fraction is not None]
    if not fractions:
        return Combined(len(tallies), None, None, None)
    return Combined(len(tallies), sum(fractions) / len(fractions), min(fractions), max(fractions))


@dataclass(frozen=True)
class Stability:
    """One question, one measure, one mode, over the runs."""

    correct: int
    measured: int
    runs: int

    @property
    def stable(self) -> bool:
        """The same verdict in every run, not_measured included."""
        return self.measured in (0, self.runs) and self.correct in (0, self.measured)


def stability(verdicts: list[Verdict]) -> Stability:
    t = tally(verdicts)
    return Stability(t.correct, t.measured, len(verdicts))
