"""The step 6 judgement, tested before anything exists for it to judge
(DR-16, SRS threat T-04).

Reads only the committed evaluation set, so every test here runs in CI.
Nothing here runs retrieval: there is none yet. Each `Produced` value below
is written by hand, or built from the set's own expected joins, and stands
for something a system might do.

Two kinds of test:

  the rule       each of the four parts -- decline, tables, warnings,
                 checks -- is seen to agree when it should and to disagree
                 when one thing is wrong.
  the instrument for each question, CAN it agree at all? A tree is built
                 from the question's own expected joins and handed to the
                 judgement with the best warnings a system could give. A
                 question that disagrees even then can never agree, and
                 that is a fact about the instrument, not about retrieval.
"""

import itertools
from pathlib import Path

import pytest

from app.core.eval_set import (
    EXPECTED_FAILURE_CONFIRMED,
    EXPECTED_FAILURE_UNEXPECTEDLY_PASSED,
    ExpectedJoin,
    Question,
    parse_eval_set,
)
from app.core.eval_step6 import (
    AGREES,
    DISAGREES,
    Produced,
    TreeRoute,
    expected_warnings,
    judge_step6,
)

EVAL_SET = Path(__file__).resolve().parents[2] / "eval" / "questions.yaml"

DECLINED = Produced(declined=True, tree_tables=frozenset(), warnings=frozenset(), routes=())


@pytest.fixture(scope="module")
def questions() -> dict[int, Question]:
    return {question.id: question for question in parse_eval_set(EVAL_SET.read_text()).questions}


def _join(start: str, end: str) -> ExpectedJoin:
    """"a.x, a.y" -> "b.x, b.y": one foreign key, written as in the set."""
    sides = []
    for side in (start, end):
        names = [name.strip().split(".") for name in side.split(",")]
        sides.append((names[0][0], tuple(column for _, column in names)))
    return ExpectedJoin(sides[0][0], sides[0][1], sides[1][0], sides[1][1])


def _route_within(joins: tuple[ExpectedJoin, ...], start: str, end: str):
    """The column pairs on the one path from `start` to `end` through
    `joins`, or None when they are not connected. Written here, apart from
    the application, so the judgement is not fed by the code it will judge."""
    reached = {start: frozenset()}
    frontier = [start]
    while frontier:
        table = frontier.pop()
        for join in joins:
            if table in (join.from_table, join.to_table):
                other = join.to_table if table == join.from_table else join.from_table
                if other not in reached:
                    reached[other] = reached[table] | frozenset(join.edges)
                    frontier.append(other)
    return reached.get(end)


def _produced(question: Question, joins: tuple[ExpectedJoin, ...], warnings=None, tables=None) -> Produced:
    """What a system would have produced had it selected exactly `joins`:
    the tree's tables, and for each pair check the path within the tree
    with the warnings the set expects of that pair. `warnings` and `tables`
    override the answer-level codes and the tree's tables."""
    if tables is None:
        tables = {table for join in joins for table in (join.from_table, join.to_table)} or set(question.tables)
    if warnings is None:
        warnings = expected_warnings(question) or frozenset()
    routes = tuple(
        TreeRoute(check.between, _route_within(joins, *check.between), check.warnings) for check in question.checks
    )
    return Produced(False, frozenset(tables), frozenset(warnings), routes)


def _trees_of_its_own_joins(question: Question):
    """Every way of taking one alternative from each of the question's
    expected joins."""
    return [tuple(choice) for choice in itertools.product(*question.joins)]


# --------------------------------------------------------------------------
# The instrument: can each question agree at all?
# --------------------------------------------------------------------------


@pytest.mark.parametrize("number", [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 16])
def test_a_tree_of_the_questions_own_joins_agrees(questions, number) -> None:
    question = questions[number]
    statuses = [judge_step6(question, _produced(question, tree)).status for tree in _trees_of_its_own_joins(question)]
    assert AGREES in statuses


def test_question_15_agrees_when_declined_and_only_then(questions) -> None:
    assert judge_step6(questions[15], DECLINED).status == AGREES

    answered = Produced(False, frozenset({"customer"}), frozenset(), ())
    verdict = judge_step6(questions[15], answered)
    assert verdict.status == DISAGREES
    assert verdict.decline_as_expected is False
    assert verdict.tables_as_expected is None


def test_recorded_question_14_cannot_agree_while_its_checks_are_read_within_one_tree(questions) -> None:
    """Recorded, not wanted. The set gives question 14 a check between
    store_returns and item and another between store_sales and item, each
    accepting the direct key, because "either item key answers the
    question". Pairwise, at step 5, both can match. Within one tree item
    hangs off one table: the other check's path is then two joins long and
    is not the one accepted. So with the best possible tree and the right
    warning, question 14 still disagrees. Reported to the owner at the
    first review stop; this test fails the day the rule changes."""
    question = questions[14]
    trees = _trees_of_its_own_joins(question)
    assert len(trees) == 2

    for tree in trees:
        verdict = judge_step6(question, _produced(question, tree))
        assert verdict.status == DISAGREES
        assert verdict.tables_as_expected is True
        assert verdict.warnings_as_expected is True
        assert verdict.checks_hold is False
        assert [check.path_accepted for check in verdict.checks].count(False) == 1


def test_question_13_agrees_only_when_both_joins_use_the_same_sales_table(questions) -> None:
    """The set says so in a comment. No rule enforces it; it follows from
    the tables: two different sales tables are two tables beyond `tables`."""
    question = questions[13]
    agreeing = [
        {join.from_table for join in tree} & set(question.tables_one_of)
        for tree in _trees_of_its_own_joins(question)
        if judge_step6(question, _produced(question, tree)).status == AGREES
    ]
    assert sorted(agreeing, key=sorted) == [{"catalog_sales"}, {"store_sales"}, {"web_sales"}]


# --------------------------------------------------------------------------
# Tables: the selected tree, exactly
# --------------------------------------------------------------------------


def test_one_table_too_many_disagrees(questions) -> None:
    question = questions[5]
    (tree,) = _trees_of_its_own_joins(question)
    verdict = judge_step6(question, _produced(question, tree, tables={"store_returns", "reason", "date_dim"}))
    assert verdict.status == DISAGREES
    assert verdict.tables_as_expected is False
    assert verdict.checks_hold is True


def test_one_table_too_few_disagrees(questions) -> None:
    question = questions[1]
    tree = (_join("store_sales.ss_store_sk", "store.s_store_sk"),)
    verdict = judge_step6(question, _produced(question, tree))
    assert verdict.status == DISAGREES
    assert verdict.tables_as_expected is False


def test_exactly_one_of_the_alternatives_is_required(questions) -> None:
    question = questions[8]
    warned = frozenset({"anchor_ambiguity"})

    def status(*tables: str) -> str:
        return judge_step6(question, Produced(False, frozenset(tables), warned, ())).status

    assert status("promotion", "web_sales") == AGREES
    assert status("promotion") == DISAGREES
    assert status("promotion", "web_sales", "store_sales") == DISAGREES
    assert status("promotion", "date_dim") == DISAGREES
    assert status("web_sales") == DISAGREES


# --------------------------------------------------------------------------
# Warnings: exactly the one expected
# --------------------------------------------------------------------------


def test_a_question_expecting_none_disagrees_with_any_warning(questions) -> None:
    question = questions[1]
    (tree,) = _trees_of_its_own_joins(question)
    for code in ("arbitrary_choice", "many_to_many", "anchor_ambiguity", "multi_anchor"):
        verdict = judge_step6(question, _produced(question, tree, warnings={code}))
        assert verdict.status == DISAGREES
        assert verdict.warnings_as_expected is False
        assert verdict.tables_as_expected is True


def test_the_expected_warning_must_be_raised(questions) -> None:
    question = questions[9]
    (tree,) = _trees_of_its_own_joins(question)
    assert judge_step6(question, _produced(question, tree)).status == AGREES
    assert judge_step6(question, _produced(question, tree, warnings=set())).status == DISAGREES


def test_the_expected_warning_with_another_beside_it_disagrees(questions) -> None:
    question = questions[9]
    (tree,) = _trees_of_its_own_joins(question)
    noisy = _produced(question, tree, warnings={"anchor_ambiguity", "multi_anchor"})
    assert judge_step6(question, noisy).status == DISAGREES


def test_see_note_states_no_warning_of_its_own(questions) -> None:
    question = questions[16]
    (tree,) = _trees_of_its_own_joins(question)
    assert expected_warnings(question) is None
    verdict = judge_step6(question, _produced(question, tree, warnings={"multi_anchor"}))
    assert verdict.warnings_as_expected is None
    assert verdict.status == AGREES


# --------------------------------------------------------------------------
# Checks: step 5's judge, on the path within the tree
# --------------------------------------------------------------------------


def test_the_wrong_pivot_disagrees_although_its_warning_is_the_expected_one(questions) -> None:
    """Question 6 through customer and not item: many_to_many is raised, as
    expected, about the wrong table."""
    question = questions[6]
    tree = (
        _join("store_sales.ss_customer_sk", "customer.c_customer_sk"),
        _join("catalog_sales.cs_bill_customer_sk", "customer.c_customer_sk"),
    )
    verdict = judge_step6(question, _produced(question, tree))
    assert verdict.status == DISAGREES
    assert verdict.warnings_as_expected is True
    assert verdict.tables_as_expected is False
    assert verdict.checks_hold is False


def test_a_pair_warning_the_check_does_not_expect_disagrees(questions) -> None:
    question = questions[2]
    (tree,) = _trees_of_its_own_joins(question)
    produced = _produced(question, tree)
    arbitrary = Produced(
        False,
        produced.tree_tables,
        frozenset(),
        (TreeRoute(produced.routes[0].between, produced.routes[0].selected, ("arbitrary_choice",)),),
    )
    verdict = judge_step6(question, arbitrary)
    assert verdict.status == DISAGREES
    assert verdict.checks_hold is False
    assert verdict.checks[0].path_accepted and not verdict.checks[0].warnings_as_expected


def test_a_table_outside_the_tree_has_no_path_and_the_check_fails(questions) -> None:
    question = questions[1]
    tree = (_join("store_sales.ss_store_sk", "store.s_store_sk"),)
    verdict = judge_step6(question, _produced(question, tree))
    assert [check.path_accepted for check in verdict.checks] == [True, False]


def test_a_predicted_failure_is_still_a_failure(questions) -> None:
    """Question 16 by wp_customer_sk, as the set predicted. Step 5's status
    for the check says the prediction held. The question disagrees."""
    question = questions[16]
    tree = (
        _join("web_sales.ws_web_page_sk", "web_page.wp_web_page_sk"),
        _join("web_page.wp_customer_sk", "customer.c_customer_sk"),
    )
    verdict = judge_step6(question, _produced(question, tree))
    assert verdict.status == DISAGREES
    assert verdict.tables_as_expected is True
    assert verdict.checks[2].status == EXPECTED_FAILURE_CONFIRMED
    assert verdict.checks[2].prediction_held is True
    assert verdict.checks[1].path_accepted is False


def test_a_predicted_failure_alone_is_enough_to_disagree(questions) -> None:
    """The test above disagrees twice over: its second check fails as well.
    Here only the predicted check fails, so nothing else can carry the
    verdict. No single tree produces this; it isolates the rule. (With the
    rule softened to "no check is a plain mismatch", the test above still
    passed. This one was added because of that.)"""
    question = questions[16]
    (tree,) = _trees_of_its_own_joins(question)
    right = _produced(question, tree)
    predicted = frozenset(_join("web_page.wp_customer_sk", "customer.c_customer_sk").edges)
    produced = Produced(
        False,
        right.tree_tables,
        frozenset(),
        (*right.routes[:2], TreeRoute(right.routes[2].between, predicted, ())),
    )
    verdict = judge_step6(question, produced)
    assert [check.status for check in verdict.checks] == ["match", "match", EXPECTED_FAILURE_CONFIRMED]
    assert verdict.checks_hold is False
    assert verdict.status == DISAGREES


def test_a_predicted_failure_that_passes_agrees_and_keeps_step_5s_status(questions) -> None:
    question = questions[16]
    (tree,) = _trees_of_its_own_joins(question)
    verdict = judge_step6(question, _produced(question, tree))
    assert verdict.status == AGREES
    assert verdict.checks[2].status == EXPECTED_FAILURE_UNEXPECTEDLY_PASSED


# --------------------------------------------------------------------------
# Decline
# --------------------------------------------------------------------------


def test_declining_an_answerable_question_disagrees(questions) -> None:
    verdict = judge_step6(questions[1], DECLINED)
    assert verdict.status == DISAGREES
    assert verdict.decline_as_expected is False
    assert verdict.checks == ()


# --------------------------------------------------------------------------
# What it refuses to judge
# --------------------------------------------------------------------------


def test_an_unknown_warning_code_raises(questions) -> None:
    with pytest.raises(ValueError, match="not warning codes"):
        judge_step6(questions[8], Produced(False, frozenset({"promotion"}), frozenset({"anchor_ambiguous"}), ()))


def test_declined_with_a_tree_raises(questions) -> None:
    with pytest.raises(ValueError, match="declined, yet"):
        judge_step6(questions[15], Produced(True, frozenset({"customer"}), frozenset(), ()))


def test_a_check_with_no_route_reported_raises(questions) -> None:
    question = questions[1]
    with pytest.raises(ValueError, match="no route was reported"):
        judge_step6(question, Produced(False, frozenset(question.tables), frozenset(), ()))
