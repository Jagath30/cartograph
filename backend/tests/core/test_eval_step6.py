"""The step 6 judgement, tested before anything exists for it to judge
(DR-16, SRS threat T-04).

Reads only the committed evaluation set, so every test here runs in CI.
Nothing here runs retrieval: there is none yet. Each `Produced` value below
is written by hand, or built from the set's own expected joins, and stands
for something a system might do.

Two kinds of test:

  the rule       each of the four parts -- decline, tables, joins,
                 warnings -- is seen to agree when it should and to
                 disagree when one thing is wrong; and the pair checks are
                 seen to be reported and to decide nothing.
  the instrument for each question, CAN it agree at all? A tree is built
                 from the question's own expected joins and handed to the
                 judgement with the best warnings a system could give. A
                 question that disagrees even then can never agree, and
                 that is a fact about the instrument, not about retrieval.
                 All fifteen answerable questions can; question 15 agrees
                 when declined.
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

DECLINED = Produced(declined=True, tree_tables=frozenset(), tree_joins=frozenset(), warnings=frozenset(), routes=())


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
    return Produced(False, frozenset(tables), _joins(joins), frozenset(warnings), routes)


def _joins(joins) -> frozenset:
    return frozenset(frozenset(join.edges) for join in joins)


def _trees_of_its_own_joins(question: Question):
    """Every way of taking one alternative from each of the question's
    expected joins."""
    return [tuple(choice) for choice in itertools.product(*question.joins)]


# --------------------------------------------------------------------------
# The instrument: can each question agree at all?
# --------------------------------------------------------------------------


@pytest.mark.parametrize("number", [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 16])
def test_a_tree_of_the_questions_own_joins_agrees(questions, number) -> None:
    question = questions[number]
    statuses = [judge_step6(question, _produced(question, tree)).status for tree in _trees_of_its_own_joins(question)]
    assert AGREES in statuses


def test_question_15_agrees_when_declined_and_only_then(questions) -> None:
    assert judge_step6(questions[15], DECLINED).status == AGREES

    answered = Produced(False, frozenset({"customer"}), frozenset(), frozenset(), ())
    verdict = judge_step6(questions[15], answered)
    assert verdict.status == DISAGREES
    assert verdict.decline_as_expected is False
    assert verdict.tables_as_expected is None


def test_question_14_agrees_with_either_item_key_and_with_no_other_tree(questions) -> None:
    """Why the tree is judged whole. The set's `joins` says question 14 may
    reach item from the return or from the sale. Its two item checks each
    accept only the direct key, so within one tree one of them always
    fails: judged pair by pair, this question could never agree, and a test
    recorded exactly that before the rule was corrected. Judged on `joins`,
    either key agrees -- and both keys, neither key, or item hung off the
    promotion do not."""
    question = questions[14]
    return_to_sale = _join(
        "store_returns.sr_item_sk, store_returns.sr_ticket_number",
        "store_sales.ss_item_sk, store_sales.ss_ticket_number",
    )
    promoted = _join("store_sales.ss_promo_sk", "promotion.p_promo_sk")
    by_return = _join("store_returns.sr_item_sk", "item.i_item_sk")
    by_sale = _join("store_sales.ss_item_sk", "item.i_item_sk")
    by_promotion = _join("promotion.p_item_sk", "item.i_item_sk")

    def verdict(*item_joins: ExpectedJoin):
        return judge_step6(question, _produced(question, (return_to_sale, promoted, *item_joins)))

    for either in (by_return, by_sale):
        agreed = verdict(either)
        assert agreed.status == AGREES
        # The pair checks still say what they said: one of the two item
        # checks fails within any one tree. It is reported and decides nothing.
        assert [check.path_accepted for check in agreed.checks].count(False) == 1

    both = verdict(by_return, by_sale)
    assert (both.status, both.tables_as_expected, both.joins_as_expected) == (DISAGREES, True, False)

    neither = judge_step6(
        question, _produced(question, (return_to_sale, promoted), tables=set(question.tables))
    )
    assert (neither.status, neither.tables_as_expected, neither.joins_as_expected) == (DISAGREES, True, False)

    other_reading = verdict(by_promotion)
    assert (other_reading.status, other_reading.tables_as_expected, other_reading.joins_as_expected) == (
        DISAGREES,
        True,
        False,
    )


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
    assert verdict.joins_as_expected is True


def test_one_table_too_few_disagrees(questions) -> None:
    question = questions[1]
    tree = (_join("store_sales.ss_store_sk", "store.s_store_sk"),)
    verdict = judge_step6(question, _produced(question, tree))
    assert verdict.status == DISAGREES
    assert verdict.tables_as_expected is False


def test_exactly_one_of_the_alternatives_is_required(questions) -> None:
    question = questions[8]
    warned = frozenset({"anchor_ambiguity"})
    by_web = _joins([_join("web_sales.ws_promo_sk", "promotion.p_promo_sk")])

    def status(*tables: str) -> str:
        return judge_step6(question, Produced(False, frozenset(tables), by_web, warned, ())).status

    assert status("promotion", "web_sales") == AGREES
    assert status("promotion") == DISAGREES
    assert status("promotion", "web_sales", "store_sales") == DISAGREES
    assert status("promotion", "date_dim") == DISAGREES
    assert status("web_sales") == DISAGREES


# --------------------------------------------------------------------------
# Joins: the set's own `joins`, exactly
# --------------------------------------------------------------------------


def test_the_right_tables_by_the_wrong_key_disagree(questions) -> None:
    """Question 4 by the ship date: the same three tables, the other key."""
    question = questions[4]
    tree = (
        _join("web_sales.ws_item_sk", "item.i_item_sk"),
        _join("web_sales.ws_ship_date_sk", "date_dim.d_date_sk"),
    )
    verdict = judge_step6(question, _produced(question, tree))
    assert (verdict.status, verdict.tables_as_expected, verdict.joins_as_expected) == (DISAGREES, True, False)


def test_a_join_beyond_the_expected_ones_disagrees(questions) -> None:
    """Question 1 with the store's closing date as well: a join a correct
    answer never makes, between two tables that are both expected."""
    question = questions[1]
    (tree,) = _trees_of_its_own_joins(question)
    extra = (*tree, _join("store.s_closed_date_sk", "date_dim.d_date_sk"))
    verdict = judge_step6(question, _produced(question, extra))
    assert (verdict.status, verdict.tables_as_expected, verdict.joins_as_expected) == (DISAGREES, True, False)


def test_a_missing_join_disagrees(questions) -> None:
    question = questions[7]
    (tree,) = _trees_of_its_own_joins(question)
    verdict = judge_step6(question, _produced(question, tree[:-1], tables=set(question.tables)))
    assert (verdict.status, verdict.tables_as_expected, verdict.joins_as_expected) == (DISAGREES, True, False)


def test_two_alternatives_of_one_join_disagree(questions) -> None:
    """Question 3 accepts the bill or the ship address. Not both."""
    question = questions[3]
    bill, ship = (alternative for alternative in question.joins[0])
    assert judge_step6(question, _produced(question, (bill,))).status == AGREES
    assert judge_step6(question, _produced(question, (ship,))).status == AGREES
    assert judge_step6(question, _produced(question, (bill, ship))).joins_as_expected is False


def test_a_composite_key_is_one_join_whatever_order_its_columns_come_in(questions) -> None:
    question = questions[14]
    reordered = _join(
        "store_returns.sr_ticket_number, store_returns.sr_item_sk",
        "store_sales.ss_ticket_number, store_sales.ss_item_sk",
    )
    half = _join("store_returns.sr_item_sk", "store_sales.ss_item_sk")
    rest = (
        _join("store_sales.ss_promo_sk", "promotion.p_promo_sk"),
        _join("store_sales.ss_item_sk", "item.i_item_sk"),
    )
    assert judge_step6(question, _produced(question, (reordered, *rest))).status == AGREES
    assert judge_step6(question, _produced(question, (half, *rest))).joins_as_expected is False


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
    assert verdict.joins_as_expected is False


def test_the_predicted_wrong_route_disagrees(questions) -> None:
    """Question 16 by wp_customer_sk, as the set predicted: the right three
    tables. Step 5's status for the check says the prediction held. The
    question disagrees, on its joins."""
    question = questions[16]
    tree = (
        _join("web_sales.ws_web_page_sk", "web_page.wp_web_page_sk"),
        _join("web_page.wp_customer_sk", "customer.c_customer_sk"),
    )
    verdict = judge_step6(question, _produced(question, tree))
    assert (verdict.status, verdict.tables_as_expected, verdict.joins_as_expected) == (DISAGREES, True, False)
    assert verdict.checks[2].status == EXPECTED_FAILURE_CONFIRMED
    assert verdict.checks[2].prediction_held is True


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
        assert verdict.joins_as_expected is True


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
# Pair checks: reported on the path within the tree, deciding nothing
# --------------------------------------------------------------------------


def test_a_failing_pair_check_is_reported_and_does_not_decide(questions) -> None:
    """The right tree for question 2, with a warning on the pair that the
    check does not expect and that the answer as a whole does not carry.
    The check's verdict says so. The question still agrees: it is decided
    by its tables, its joins and its own warning."""
    question = questions[2]
    (tree,) = _trees_of_its_own_joins(question)
    produced = _produced(question, tree)
    route = TreeRoute(produced.routes[0].between, produced.routes[0].selected, ("arbitrary_choice",))
    verdict = judge_step6(
        question, Produced(False, produced.tree_tables, produced.tree_joins, frozenset(), (route,))
    )
    assert verdict.checks[0].status == "mismatch"
    assert verdict.checks[0].path_accepted and not verdict.checks[0].warnings_as_expected
    assert verdict.status == AGREES


def test_a_table_outside_the_tree_has_no_path(questions) -> None:
    question = questions[1]
    tree = (_join("store_sales.ss_store_sk", "store.s_store_sk"),)
    verdict = judge_step6(question, _produced(question, tree))
    assert [check.path_accepted for check in verdict.checks] == [True, False]


def test_a_predicted_failure_that_passes_keeps_step_5s_status(questions) -> None:
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
    produced = Produced(False, frozenset({"promotion"}), frozenset(), frozenset({"anchor_ambiguous"}), ())
    with pytest.raises(ValueError, match="not warning codes"):
        judge_step6(questions[8], produced)


def test_declined_with_a_tree_raises(questions) -> None:
    with pytest.raises(ValueError, match="declined, yet"):
        judge_step6(questions[15], Produced(True, frozenset({"customer"}), frozenset(), frozenset(), ()))


def test_a_check_with_no_route_reported_raises(questions) -> None:
    question = questions[1]
    (tree,) = _trees_of_its_own_joins(question)
    with pytest.raises(ValueError, match="no route was reported"):
        judge_step6(question, Produced(False, frozenset(question.tables), _joins(tree), frozenset(), ()))
