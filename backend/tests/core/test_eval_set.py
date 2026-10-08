"""The evaluation set, guarded (DR-16, SRS threat T-04).

Reads only committed files -- the set, the DDL and the generated overlay --
so every test here runs in CI, which has no warehouse.

Two of these are the guard itself:

  the hash    The set's sha256 is pinned below, in this file. Changing one
              byte of the set fails the suite until the value here is
              changed too, so an edited expectation and the edit to this
              test always sit in the same diff, in front of whoever reviews
              it. It cannot stop an edit. It stops a quiet one.
  the names   Every table, column and join the set names exists in the DDL
              and the overlay. An expectation with a typo in it can never
              match, and would read as the system's failure, not the set's.

Nothing here runs the PathFinder. Whether the system agrees with the set is
reported by `python -m app.show_eval`; it is a report and not a test,
because disagreement is expected and is the point.
"""

import hashlib
from pathlib import Path

import pytest
import yaml
from tpcds_files import OVERLAY, ddl_snapshot

from app.core.eval_set import (
    EXPECTED_FAILURE_CONFIRMED,
    EXPECTED_FAILURE_UNEXPECTEDLY_PASSED,
    MATCH,
    MISMATCH,
    NO_MECHANISM,
    NOT_EVALUABLE,
    edges_of,
    judge,
    judge_question,
    parse_eval_set,
)
from app.core.overlay import apply_overlay, parse_overlay

EVAL_SET = Path(__file__).resolve().parents[2] / "eval" / "questions.yaml"

# THE FREEZE. If this assertion fails, backend/eval/questions.yaml changed.
# From the moment step 6 begins that is forbidden (DR-16): an expectation is
# never edited to match what the system does. Do not update this value to
# make the test pass. Before step 6, a genuinely wrong expectation may be
# corrected on the owner's decision, with the reason in CHECKPOINTS.md --
# and then this value changes in the same commit, with that reason.
PINNED_SHA256 = "a92f27c2f3fee9c1d178e21504eadc87aa9ec6a622e896561064e798d1100440"


@pytest.fixture(scope="module")
def eval_set():
    return parse_eval_set(EVAL_SET.read_text())


@pytest.fixture(scope="module")
def snapshot():
    return apply_overlay(ddl_snapshot(), parse_overlay(OVERLAY.read_text()))


# --------------------------------------------------------------------------
# The guard
# --------------------------------------------------------------------------


def test_the_set_is_byte_for_byte_the_one_that_was_frozen() -> None:
    assert hashlib.sha256(EVAL_SET.read_bytes()).hexdigest() == PINNED_SHA256, (
        f"{EVAL_SET.name} is not the file that was frozen. Read the comment above PINNED_SHA256 before doing anything."
    )


def test_every_table_column_and_join_the_set_names_exists(eval_set, snapshot) -> None:
    tables = {table.name for table in snapshot.tables}
    columns = {(column.table, column.name) for column in snapshot.columns}
    foreign_keys = {(key.from_table, key.from_columns, key.to_table, key.to_columns) for key in snapshot.foreign_keys}

    assert sorted(eval_set.tables_named - tables) == []
    assert sorted(eval_set.columns_named - columns) == []
    # Two real columns paired wrongly are still a typo. The order of a
    # composite key's columns is not part of what a join means, so compare
    # the pairs.
    real = {
        frozenset((f"{from_table}.{start}", f"{to_table}.{end}") for start, end in zip(from_columns, to_columns))
        for from_table, from_columns, to_table, to_columns in foreign_keys
    }
    invented = [join for join in eval_set.joins_named if frozenset(join.edges) not in real]
    assert invented == []


# --------------------------------------------------------------------------
# The set is what it says it is
# --------------------------------------------------------------------------


def test_the_set_holds_sixteen_questions_and_says_where_they_came_from(eval_set) -> None:
    assert len(eval_set.questions) == 16
    assert "approved by him, 8-9 October 2026" in eval_set.provenance
    assert "No expectation was computed by running the PathFinder" in eval_set.derivation


def test_only_what_the_set_declared_in_advance_is_an_expected_failure(eval_set) -> None:
    """Pinned so that a later hand cannot quietly excuse a disagreement by
    marking it expected: the list of excuses is exactly this."""
    marked = [
        (question.id, check.between) for question in eval_set.questions for check in question.checks if check.expected_to_fail
    ]
    assert marked == [(16, ("web_page", "customer"))]

    without_mechanism = {
        question.id: question.warning
        for question in eval_set.questions
        if eval_set.warning_categories[question.warning] == NO_MECHANISM
    }
    assert without_mechanism == {
        8: "anchor_ambiguity",
        9: "anchor_ambiguity",
        13: "anchor_ambiguity",
        14: "multi_anchor",
        15: "decline",
    }


def test_a_question_that_cannot_be_judged_whole_is_never_called_checkable(eval_set) -> None:
    by_id = {question.id: question.at_step_5 for question in eval_set.questions}
    assert {number: state for number, state in by_id.items() if state != "checkable"} == {
        8: "not_evaluable",
        13: "partial",
        15: "not_evaluable",
    }


# --------------------------------------------------------------------------
# Judging
# --------------------------------------------------------------------------


def _check(eval_set, question: int, *between: str):
    return next(check for check in eval_set.questions[question - 1].checks if check.between == between)


def test_a_check_matches_only_on_the_path_and_the_warnings_together(eval_set) -> None:
    check = _check(eval_set, 2, "catalog_sales", "customer_address")
    billed = edges_of(check.accept[0])
    shipped = frozenset({("catalog_sales.cs_ship_addr_sk", "customer_address.ca_address_sk")})

    assert judge(check, billed, ()).status == MATCH
    # The right path with a warning the set does not expect is a mismatch...
    wrong_warning = judge(check, billed, ("arbitrary_choice",))
    assert (wrong_warning.status, wrong_warning.path_accepted, wrong_warning.warnings_as_expected) == (MISMATCH, True, False)
    # ...and so is the wrong path, and so is no path at all.
    assert judge(check, shipped, ()).status == MISMATCH
    assert judge(check, None, ("no_path",)).status == MISMATCH


def test_a_check_may_accept_several_paths_and_still_require_its_warning(eval_set) -> None:
    check = _check(eval_set, 3, "catalog_sales", "customer_address")
    for path in check.accept:
        assert judge(check, edges_of(path), ("arbitrary_choice",)).status == MATCH
        assert judge(check, edges_of(path), ()).status == MISMATCH


def test_an_expected_failure_is_confirmed_or_unexpectedly_passes_and_its_prediction_is_judged_separately(
    eval_set,
) -> None:
    check = _check(eval_set, 16, "web_page", "customer")
    predicted = edges_of(check.prediction.path)
    correct = edges_of(check.accept[0])
    elsewhere = frozenset({("web_returns.wr_web_page_sk", "web_page.wp_web_page_sk")})

    as_predicted = judge(check, predicted, ())
    assert (as_predicted.status, as_predicted.prediction_held) == (EXPECTED_FAILURE_CONFIRMED, True)
    # It failed, but not the way the set said it would.
    differently = judge(check, elsewhere, ())
    assert (differently.status, differently.prediction_held) == (EXPECTED_FAILURE_CONFIRMED, False)
    passed = judge(check, correct, ())
    assert (passed.status, passed.prediction_held) == (EXPECTED_FAILURE_UNEXPECTEDLY_PASSED, False)


def test_a_partly_checked_question_is_not_evaluable_however_well_its_checks_did(eval_set) -> None:
    question = eval_set.questions[12]
    assert question.id == 13
    matched = tuple(judge(check, edges_of(check.accept[0]), ()) for check in question.checks)
    assert [verdict.status for verdict in matched] == [MATCH]
    assert judge_question(question, eval_set.warning_categories, matched, frozenset()) == NOT_EVALUABLE


def test_a_question_whose_warning_nothing_can_raise_is_an_expected_failure_not_a_match(eval_set) -> None:
    question = eval_set.questions[8]
    assert (question.id, question.warning) == (9, "anchor_ambiguity")
    matched = tuple(judge(check, edges_of(check.accept[0]), ()) for check in question.checks)
    categories = eval_set.warning_categories

    assert judge_question(question, categories, matched, frozenset()) == EXPECTED_FAILURE_CONFIRMED
    assert (
        judge_question(question, categories, matched, frozenset({"anchor_ambiguity"}))
        == EXPECTED_FAILURE_UNEXPECTEDLY_PASSED
    )
    # A mismatch underneath is still a mismatch: an expected failure never hides one.
    broken = (judge(question.checks[0], None, ("no_path",)),)
    assert judge_question(question, categories, broken, frozenset()) == MISMATCH


# --------------------------------------------------------------------------
# The parser refuses what would quietly weaken the set
# --------------------------------------------------------------------------


def _document() -> dict:
    return yaml.safe_load(EVAL_SET.read_text())


def _parse(document: dict):
    return parse_eval_set(yaml.safe_dump(document))


def test_the_committed_set_survives_a_round_trip_through_the_helper() -> None:
    """So that each refusal below is caused by its one change and nothing else."""
    assert len(_parse(_document()).questions) == 16


def test_a_set_without_its_provenance_is_refused() -> None:
    document = _document()
    del document["provenance"]
    with pytest.raises(ValueError, match="provenance"):
        _parse(document)


def test_a_question_without_a_warning_is_refused() -> None:
    document = _document()
    del document["questions"][0]["warning"]
    with pytest.raises(ValueError, match="warning"):
        _parse(document)


def test_a_field_the_parser_does_not_understand_is_refused() -> None:
    document = _document()
    document["questions"][0]["expected_path"] = "store_sales -> store"
    with pytest.raises(ValueError, match="expected_path"):
        _parse(document)


def test_a_path_that_does_not_connect_its_two_tables_is_refused() -> None:
    document = _document()
    document["questions"][0]["checks"][0]["between"] = ["store_sales", "item"]
    with pytest.raises(ValueError, match="does not run from store_sales to item"):
        _parse(document)


def test_an_expected_failure_without_a_prediction_is_refused() -> None:
    document = _document()
    document["questions"][0]["checks"][0]["expected_to_fail"] = True
    with pytest.raises(ValueError, match="go together"):
        _parse(document)


def test_a_dropped_question_is_refused_by_its_missing_number() -> None:
    document = _document()
    del document["questions"][4]
    with pytest.raises(ValueError, match="ids must run"):
        _parse(document)
