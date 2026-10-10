"""The keep-rule of the retrieval pass, on the committed evaluation set with
trees written by hand. No database and no retrieval: runs in CI."""

import pytest

from app.core.eval_set import parse_eval_set
from app.core.eval_step6 import Produced
from app.core.keep_rule import held, keep
from tests.core.tpcds_files import BACKEND

QUESTIONS = {question.id: question for question in parse_eval_set((BACKEND / "eval" / "questions.yaml").read_text()).questions}


def _own_tree(number: int, extra_tables=(), extra_joins=(), warnings=None, pick: int = 0) -> Produced:
    """A question's own expected tree, the first alternative of every
    `one_of` unless told, with its expected warning."""
    question = QUESTIONS[number]
    joins = [alternatives[min(pick, len(alternatives) - 1)] for alternatives in question.joins]
    tables = {table for join in joins for table in (join.from_table, join.to_table)} | set(question.tables)
    if warnings is None:
        warnings = () if question.warning in ("none", "see_note") else (question.warning,)
    return Produced(
        False,
        frozenset(tables | set(extra_tables)),
        frozenset(frozenset(join.edges) for join in joins) | frozenset(extra_joins),
        frozenset(warnings),
        (),
    )


DATE_JOIN = frozenset({("store_returns.sr_returned_date_sk", "date_dim.d_date_sk")})


def test_a_question_answered_exactly_holds_every_part() -> None:
    assert held(QUESTIONS[1], _own_tree(1)) == {
        "declined as expected", "table store_sales", "table store", "table date_dim",
        "join store_sales.ss_store_sk = store.s_store_sk",
        "join store_sales.ss_sold_date_sk = date_dim.d_date_sk",
        "no warning", "exact tables", "exact joins",
    }  # fmt: skip


def test_a_surplus_table_costs_the_exact_parts_and_nothing_else() -> None:
    with_surplus = held(QUESTIONS[1], _own_tree(1, extra_tables=("store_returns",), extra_joins=(DATE_JOIN,)))
    assert held(QUESTIONS[1], _own_tree(1)) - with_surplus == {"exact tables", "exact joins"}


def test_a_missing_table_and_its_join_are_not_held() -> None:
    whole = _own_tree(1)
    without = Produced(
        False,
        whole.tree_tables - {"date_dim"},
        frozenset(join for join in whole.tree_joins if ("store_sales.ss_sold_date_sk", "date_dim.d_date_sk") not in join),
        frozenset(),
        (),
    )
    assert held(QUESTIONS[1], whole) - held(QUESTIONS[1], without) == {
        "table date_dim", "join store_sales.ss_sold_date_sk = date_dim.d_date_sk", "exact tables", "exact joins",
    }  # fmt: skip


def test_a_question_that_expects_no_warning_loses_that_when_one_is_raised() -> None:
    quiet, loud = held(QUESTIONS[5], _own_tree(5)), held(QUESTIONS[5], _own_tree(5, warnings=("many_to_many",)))
    assert quiet - loud == {"no warning"}


def test_an_expected_warning_is_held_when_raised_among_others_and_not_when_absent() -> None:
    assert "warning arbitrary_choice" in held(QUESTIONS[3], _own_tree(3, warnings=("arbitrary_choice", "multi_anchor")))
    assert "warning arbitrary_choice" not in held(QUESTIONS[3], _own_tree(3, warnings=("multi_anchor",)))
    assert "no warning" not in held(QUESTIONS[3], _own_tree(3, warnings=()))


def test_a_one_of_join_is_held_by_any_alternative_under_one_name() -> None:
    bill, ship = held(QUESTIONS[3], _own_tree(3, pick=0)), held(QUESTIONS[3], _own_tree(3, pick=1))
    assert bill == ship
    assert "join catalog_sales.cs_bill_addr_sk = customer_address.ca_address_sk" in bill


def test_question_8s_exact_tree_is_held_and_three_sales_tables_lose_it() -> None:
    exact = held(QUESTIONS[8], _own_tree(8, warnings=()))
    assert {"exact tables", "exact joins", "table promotion", "table one of store_sales, catalog_sales, web_sales"} <= exact
    web = frozenset({("web_sales.ws_promo_sk", "promotion.p_promo_sk")})
    crowded = held(QUESTIONS[8], _own_tree(8, extra_tables=("web_sales",), extra_joins=(web,), warnings=()))
    assert exact - crowded == {"exact tables", "exact joins"}


def test_question_15_holds_its_decline_only_when_declined() -> None:
    assert held(QUESTIONS[15], Produced(True, frozenset(), frozenset(), frozenset(), ())) == {"declined as expected"}
    assert held(QUESTIONS[15], Produced(False, frozenset({"customer"}), frozenset(), frozenset(), ())) == frozenset()


def test_an_answerable_question_declined_holds_nothing() -> None:
    assert held(QUESTIONS[1], Produced(True, frozenset(), frozenset(), frozenset(), ())) == frozenset()


def test_see_note_holds_no_warning_item_either_way() -> None:
    assert not {item for item in held(QUESTIONS[16], _own_tree(16)) if "warning" in item}


def test_kept_needs_a_gain_and_no_loss() -> None:
    base = {1: frozenset({"a", "b"}), 2: frozenset({"c"})}
    assert keep(base, {1: frozenset({"a", "b", "x"}), 2: frozenset({"c"})}).kept
    assert not keep(base, base).kept  # nothing gained
    lost = keep(base, {1: frozenset({"a", "x"}), 2: frozenset({"c", "y"})})
    assert not lost.kept  # a gain elsewhere does not pay for a loss
    assert lost.lost == {1: ("b",)} and lost.gained == {1: ("x",), 2: ("y",)}


def test_a_development_table_brought_in_counts_as_a_gain_and_never_excuses_a_loss() -> None:
    base = {1: frozenset({"a"})}
    assert keep(base, base, other_gains=("d3 is shown catalog_sales",)).kept
    assert not keep(base, {1: frozenset()}, other_gains=("d3 is shown catalog_sales",)).kept


def test_the_two_runs_must_cover_the_same_questions() -> None:
    with pytest.raises(ValueError):
        keep({1: frozenset()}, {2: frozenset()})


def test_a_one_of_table_is_not_held_when_none_of_them_is_in_the_tree() -> None:
    alone = Produced(False, frozenset({"promotion"}), frozenset(), frozenset(), ())
    assert held(QUESTIONS[8], alone) == {"declined as expected", "table promotion"}
