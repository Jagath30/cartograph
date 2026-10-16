"""The step 10 judgement, on hand-written SQL.

Every scoring rule the owner approved at stop 1 is held here, against
questions of the frozen original set and against held-out-SHAPED questions
built by hand. backend/eval/heldout.yaml is not read: its questions are
not seen before the protocol is committed.
"""

from pathlib import Path

import pytest
from tpcds_files import OVERLAY, ddl_snapshot

from app.core.conformance import extract_joins
from app.core.eval_set import ExpectedJoin, parse_eval_set
from app.core.eval_step10 import (
    CORRECT,
    EXCLUDED,
    HELDOUT,
    NOT_MEASURED,
    ORIGINAL,
    WRONG,
    Answer,
    Tally,
    choices_of,
    combine,
    from_heldout,
    from_original,
    key_columns,
    referenced_tables,
    retrieval_recall,
    score,
    stability,
    tally,
)
from app.core.heldout import HeldOutQuestion
from app.core.overlay import apply_overlay, parse_overlay
from app.core.sql_reading import schema_of

QUESTIONS = Path(__file__).resolve().parents[2] / "eval" / "questions.yaml"


@pytest.fixture(scope="module")
def snapshot():
    return apply_overlay(ddl_snapshot(), parse_overlay(OVERLAY.read_text()))


@pytest.fixture(scope="module")
def schema(snapshot):
    return schema_of(snapshot)


@pytest.fixture(scope="module")
def keys(snapshot):
    return key_columns(snapshot)


@pytest.fixture(scope="module")
def original():
    return {str(q.id): from_original(q) for q in parse_eval_set(QUESTIONS.read_text()).questions}


def _join(from_, to) -> ExpectedJoin:
    def side(value):
        names = [value] if isinstance(value, str) else value
        return names[0].split(".")[0], tuple(name.split(".")[1] for name in names)

    (ft, fc), (tt, tc) = side(from_), side(to)
    return ExpectedJoin(ft, fc, tt, tc)


# Held-out-SHAPED questions, written here. Not the held-out set.
ONE_TABLE = from_heldout(HeldOutQuestion("H90", "How many items are there in each category?", ("item",), (), False))
COMPOSITE = from_heldout(
    HeldOutQuestion(
        "H91",
        "How many store sales were later returned, by item?",
        ("store_returns", "store_sales"),
        (_join(["store_returns.sr_item_sk", "store_returns.sr_ticket_number"],
               ["store_sales.ss_item_sk", "store_sales.ss_ticket_number"]),),  # fmt: skip
        False,
    )
)
DECLINED = from_heldout(HeldOutQuestion("H92", "Which customers liked our tweets?", (), (), True))


def judge(expectation, sql, schema, keys, outcome="answered"):
    return score(expectation, Answer(outcome, sql), schema, keys)


# --------------------------------------------------------------------------
# The sets as step 10 sees them
# --------------------------------------------------------------------------


def test_only_question_14_is_excluded_and_why_is_said() -> None:
    assert set(EXCLUDED) == {(ORIGINAL, "14")}
    assert "item 54" in EXCLUDED[(ORIGINAL, "14")] and "no" in EXCLUDED[(ORIGINAL, "14")]


def test_the_original_set_maps_whole_and_only_question_15_expects_a_decline(original) -> None:
    assert len(original) == 16
    assert [key for key, q in original.items() if q.decline] == ["15"]
    assert original["14"].excluded and not any(q.excluded for key, q in original.items() if key != "14")
    assert original["8"].tables_one_of == ("store_sales", "catalog_sales", "web_sales")
    assert all(q.set == ORIGINAL for q in original.values())


def test_a_held_out_question_maps_each_join_to_one_entry_of_one_alternative() -> None:
    assert COMPOSITE.set == HELDOUT and COMPOSITE.tables_one_of == ()
    assert len(COMPOSITE.joins) == 1 and len(COMPOSITE.joins[0]) == 1
    assert DECLINED.decline and not DECLINED.tables


def test_every_choice_of_alternatives_is_made_once(original) -> None:
    assert len(choices_of(original["13"].joins)) == 9
    assert len(choices_of(original["8"].joins)) == 3
    assert len(choices_of(original["1"].joins)) == 1
    assert choices_of(()) == ((),)


# --------------------------------------------------------------------------
# Tables and path on SQL that reads cleanly
# --------------------------------------------------------------------------

Q1_RIGHT = """
SELECT s.s_store_name, SUM(ss.ss_net_paid)
FROM store_sales ss JOIN store s ON ss.ss_store_sk = s.s_store_sk
JOIN date_dim d ON ss.ss_sold_date_sk = d.d_date_sk
WHERE d.d_year = 2002 GROUP BY s.s_store_name
"""


def test_the_expected_tables_and_joins_are_correct_on_both(original, schema, keys) -> None:
    got = judge(original["1"], Q1_RIGHT, schema, keys)
    assert (got.tables, got.path, got.why) == (CORRECT, CORRECT, "read")
    assert got.sql_tables == ("date_dim", "store", "store_sales")


def test_the_where_style_of_joining_is_read_the_same(original, schema, keys) -> None:
    sql = """SELECT s.s_store_name, SUM(ss.ss_net_paid) FROM store_sales ss, store s, date_dim d
             WHERE ss.ss_store_sk = s.s_store_sk AND ss.ss_sold_date_sk = d.d_date_sk AND d.d_year = 2002
             GROUP BY s.s_store_name"""
    got = judge(original["1"], sql, schema, keys)
    assert (got.tables, got.path) == (CORRECT, CORRECT)


def test_one_table_too_many_is_wrong_on_both(original, schema, keys) -> None:
    sql = Q1_RIGHT.replace("WHERE", "JOIN item i ON ss.ss_item_sk = i.i_item_sk WHERE")
    got = judge(original["1"], sql, schema, keys)
    assert (got.tables, got.path) == (WRONG, WRONG)
    assert got.choices[0].conformance.outcome == "diverged"


def test_one_table_too_few_is_wrong_on_both(original, schema, keys) -> None:
    sql = """SELECT s.s_store_name, SUM(ss.ss_net_paid) FROM store_sales ss
             JOIN store s ON ss.ss_store_sk = s.s_store_sk GROUP BY s.s_store_name"""
    got = judge(original["1"], sql, schema, keys)
    assert (got.tables, got.path) == (WRONG, WRONG)
    assert got.choices[0].conformance.outcome == "incomplete"


def test_the_right_tables_by_the_wrong_key_is_tables_correct_and_path_wrong(original, schema, keys) -> None:
    sql = """SELECT ca.ca_state, COUNT(*) FROM catalog_sales cs
             JOIN customer_address ca ON cs.cs_ship_addr_sk = ca.ca_address_sk GROUP BY ca.ca_state"""
    got = judge(original["2"], sql, schema, keys)
    assert (got.tables, got.path) == (CORRECT, WRONG)


def test_either_alternative_of_a_one_of_conforms(original, schema, keys) -> None:
    for key in ("cs_bill_addr_sk", "cs_ship_addr_sk"):
        sql = f"""SELECT ca.ca_state, COUNT(*) FROM catalog_sales cs
                  JOIN customer_address ca ON cs.{key} = ca.ca_address_sk GROUP BY ca.ca_state"""
        got = judge(original["3"], sql, schema, keys)
        assert (got.tables, got.path) == (CORRECT, CORRECT), key


def test_a_table_read_only_in_a_subquery_counts_as_read(original, schema, keys) -> None:
    sql = """SELECT d.d_fy_quarter_seq, SUM(ss.ss_net_paid) FROM store_sales ss
             JOIN date_dim d ON ss.ss_sold_date_sk = d.d_date_sk
             WHERE d.d_year = (SELECT MAX(d2.d_year) FROM date_dim d2) GROUP BY d.d_fy_quarter_seq"""
    got = judge(original["11"], sql, schema, keys)
    assert (got.tables, got.path) == (CORRECT, CORRECT)

    sql = """SELECT SUM(ss.ss_net_paid) FROM store_sales ss
             WHERE ss.ss_sold_date_sk IN (SELECT d.d_date_sk FROM date_dim d WHERE d.d_year = 2002)"""
    got = judge(original["11"], sql, schema, keys)
    assert got.sql_tables == ("date_dim", "store_sales")
    assert got.tables == CORRECT
    assert got.path == NOT_MEASURED  # a semi-join: subquery_predicate (item 61)


# --------------------------------------------------------------------------
# Expectations with a choice
# --------------------------------------------------------------------------


def test_question_8_wants_exactly_one_channel(original, schema, keys) -> None:
    one = """SELECT p.p_promo_name, SUM(ss.ss_net_paid) FROM store_sales ss
             JOIN promotion p ON ss.ss_promo_sk = p.p_promo_sk GROUP BY p.p_promo_name"""
    assert (judge(original["8"], one, schema, keys).tables, judge(original["8"], one, schema, keys).path) == (
        CORRECT, CORRECT,
    )  # fmt: skip

    three = """SELECT p.p_promo_name, SUM(x.amount) FROM (
                 SELECT ss.ss_promo_sk AS promo, ss.ss_net_paid AS amount FROM store_sales ss
                 UNION ALL SELECT cs.cs_promo_sk, cs.cs_ext_sales_price FROM catalog_sales cs
                 UNION ALL SELECT ws.ws_promo_sk, ws.ws_net_paid FROM web_sales ws) x
               JOIN promotion p ON x.promo = p.p_promo_sk GROUP BY p.p_promo_name"""
    got = judge(original["8"], three, schema, keys)
    assert got.tables == WRONG

    none = "SELECT p.p_promo_name FROM promotion p"
    got = judge(original["8"], none, schema, keys)
    assert (got.tables, got.path) == (WRONG, WRONG)


def test_question_8_with_a_date_it_does_not_list_is_wrong_on_both(original, schema, keys) -> None:
    sql = """SELECT d.d_year, SUM(ss.ss_net_paid) FROM store_sales ss
             JOIN promotion p ON ss.ss_promo_sk = p.p_promo_sk
             JOIN date_dim d ON ss.ss_sold_date_sk = d.d_date_sk GROUP BY d.d_year"""
    got = judge(original["8"], sql, schema, keys)
    assert (got.tables, got.path) == (WRONG, WRONG)


def test_question_13_holds_with_one_sales_table_for_both_joins(original, schema, keys) -> None:
    same = """SELECT i.i_category, SUM(cs.cs_ext_sales_price) FROM catalog_sales cs
              JOIN household_demographics hd ON cs.cs_bill_hdemo_sk = hd.hd_demo_sk
              JOIN income_band ib ON hd.hd_income_band_sk = ib.ib_income_band_sk
              JOIN item i ON cs.cs_item_sk = i.i_item_sk
              WHERE ib.ib_upper_bound = (SELECT MAX(ib2.ib_upper_bound) FROM income_band ib2)
              GROUP BY i.i_category"""
    got = judge(original["13"], same, schema, keys)
    assert (got.tables, got.path) == (CORRECT, CORRECT)
    assert [choice.conformance.outcome for choice in got.choices].count("conforms") == 1


def test_question_13_by_the_ship_side_household_is_path_wrong(original, schema, keys) -> None:
    ship = """SELECT i.i_category, SUM(cs.cs_ext_sales_price) FROM catalog_sales cs
              JOIN household_demographics hd ON cs.cs_ship_hdemo_sk = hd.hd_demo_sk
              JOIN income_band ib ON hd.hd_income_band_sk = ib.ib_income_band_sk
              JOIN item i ON cs.cs_item_sk = i.i_item_sk GROUP BY i.i_category"""
    got = judge(original["13"], ship, schema, keys)
    assert (got.tables, got.path) == (CORRECT, WRONG)


def test_question_14_is_scored_as_any_other_and_either_item_key_conforms(original, schema, keys) -> None:
    """Excluded from the tallies, not from being read: its raw results are
    reported apart."""
    for item_key in ("sr.sr_item_sk", "ss.ss_item_sk"):
        sql = f"""SELECT i.i_item_sk, COUNT(*) FROM store_returns sr
                  JOIN store_sales ss ON sr.sr_item_sk = ss.ss_item_sk AND sr.sr_ticket_number = ss.ss_ticket_number
                  JOIN promotion p ON ss.ss_promo_sk = p.p_promo_sk
                  JOIN item i ON {item_key} = i.i_item_sk GROUP BY i.i_item_sk"""
        got = judge(original["14"], sql, schema, keys)
        assert (got.tables, got.path) == (CORRECT, CORRECT), item_key


def test_half_a_two_column_key_is_path_wrong(schema, keys) -> None:
    sql = """SELECT ss.ss_item_sk, COUNT(*) FROM store_returns sr
             JOIN store_sales ss ON sr.sr_item_sk = ss.ss_item_sk GROUP BY ss.ss_item_sk"""
    got = judge(COMPOSITE, sql, schema, keys)
    assert (got.tables, got.path) == (CORRECT, WRONG)
    assert got.choices[0].conformance.outcome == "diverged"


def test_the_whole_two_column_key_conforms(schema, keys) -> None:
    sql = """SELECT ss.ss_item_sk, COUNT(*) FROM store_returns sr
             JOIN store_sales ss ON sr.sr_item_sk = ss.ss_item_sk AND sr.sr_ticket_number = ss.ss_ticket_number
             GROUP BY ss.ss_item_sk"""
    got = judge(COMPOSITE, sql, schema, keys)
    assert (got.tables, got.path) == (CORRECT, CORRECT)


def test_a_question_of_one_table_and_no_join(schema, keys) -> None:
    got = judge(ONE_TABLE, "SELECT i.i_category, COUNT(*) FROM item i GROUP BY i.i_category", schema, keys)
    assert (got.tables, got.path) == (CORRECT, CORRECT)
    joined = """SELECT i.i_category, COUNT(*) FROM item i JOIN store_sales ss ON ss.ss_item_sk = i.i_item_sk
                GROUP BY i.i_category"""
    got = judge(ONE_TABLE, joined, schema, keys)
    assert (got.tables, got.path) == (WRONG, WRONG)


# --------------------------------------------------------------------------
# Declines and failures
# --------------------------------------------------------------------------


@pytest.mark.parametrize("which", ["15", "held-out"])
def test_a_question_expected_to_be_declined(original, schema, keys, which) -> None:
    expectation = original["15"] if which == "15" else DECLINED
    got = judge(expectation, None, schema, keys, outcome="not_answerable")
    assert (got.tables, got.path, got.why) == (CORRECT, CORRECT, "declined as expected")

    got = judge(expectation, "SELECT c.c_customer_id FROM customer c", schema, keys)
    assert (got.tables, got.path, got.why) == (WRONG, WRONG, "not declined")

    for outcome in ("validation_failed", "model_failed"):
        got = judge(expectation, None, schema, keys, outcome=outcome)
        assert (got.tables, got.path, got.why) == (WRONG, WRONG, outcome)


def test_any_other_question_declined_is_wrong_on_both(original, schema, keys) -> None:
    for expectation in (original["1"], ONE_TABLE):
        got = judge(expectation, None, schema, keys, outcome="not_answerable")
        assert (got.tables, got.path, got.why) == (WRONG, WRONG, "declined")


@pytest.mark.parametrize("outcome", ["validation_failed", "model_failed"])
def test_a_failure_that_leaves_no_accepted_sql_is_wrong_on_both(original, schema, keys, outcome) -> None:
    got = judge(original["1"], None, schema, keys, outcome=outcome)
    assert (got.tables, got.path, got.why) == (WRONG, WRONG, outcome)
    assert got.choices == ()


def test_an_execution_failure_is_scored_on_its_sql_like_any_answer(original, schema, keys) -> None:
    """The owner's ruling 1 at stop 2's review: a timeout depends on the
    machine and the scale factor, not on the method. DD-20 measures the
    SQL."""
    got = judge(original["1"], Q1_RIGHT, schema, keys, outcome="execution_failed")
    assert (got.tables, got.path, got.why) == (CORRECT, CORRECT, "read")
    crossed = """SELECT s.s_store_name, SUM(ss.ss_net_paid) FROM store_sales ss CROSS JOIN store s
                 JOIN date_dim d ON ss.ss_sold_date_sk = d.d_date_sk GROUP BY s.s_store_name"""
    got = judge(original["1"], crossed, schema, keys, outcome="execution_failed")
    assert (got.tables, got.path) == (CORRECT, WRONG)
    assert [choice.conformance.outcome for choice in got.choices] == ["diverged"]


def test_an_execution_failure_on_a_question_expected_to_be_declined_is_not_a_decline(
    original, schema, keys
) -> None:
    got = judge(original["15"], "SELECT c.c_customer_id FROM customer c", schema, keys, outcome="execution_failed")
    assert (got.tables, got.path, got.why) == (WRONG, WRONG, "not declined")


def test_an_outcome_that_is_not_one_raises(original, schema, keys) -> None:
    with pytest.raises(ValueError):
        judge(original["1"], Q1_RIGHT, schema, keys, outcome="ceiling")
    with pytest.raises(ValueError):
        judge(original["1"], None, schema, keys, outcome="answered")
    with pytest.raises(ValueError):
        judge(original["1"], None, schema, keys, outcome="execution_failed")


# --------------------------------------------------------------------------
# not_checked, and what leaves which denominator
# --------------------------------------------------------------------------


def test_not_checked_leaves_the_path_denominator_and_not_the_tables_one(original, schema, keys) -> None:
    sql = """SELECT s.s_store_name, SUM(ss.ss_net_paid) FROM store_sales ss, store s, date_dim d
             WHERE (ss.ss_store_sk = s.s_store_sk OR ss.ss_store_sk IS NULL)
             AND ss.ss_sold_date_sk = d.d_date_sk GROUP BY s.s_store_name"""
    got = judge(original["1"], sql, schema, keys)
    assert got.path == NOT_MEASURED
    assert got.tables == CORRECT and got.tables_unresolved == ()


def test_a_table_reference_the_reader_cannot_resolve_leaves_the_tables_denominator_too(
    original, schema, keys
) -> None:
    sql = """SELECT s.s_store_name, g.n FROM store s CROSS JOIN generate_series(1, 3) AS g(n)"""
    tables, unresolved = referenced_tables(extract_joins(sql, schema))
    assert tables == ("store",) and unresolved == ("unread_source",)
    got = judge(original["1"], sql, schema, keys)
    assert got.tables == NOT_MEASURED


@pytest.mark.parametrize(
    "sql, reason",
    [("SELECT * FROM (", "not_parsed"), ("SELECT 1; SELECT 2", "not_one_statement"),
     ("DELETE FROM store", "not_a_select")],  # fmt: skip
)
def test_text_that_is_not_one_select_resolves_no_table(original, schema, keys, sql, reason) -> None:
    assert referenced_tables(extract_joins(sql, schema)) == ((), (reason,))
    got = judge(original["1"], sql, schema, keys)
    assert (got.tables, got.path) == (NOT_MEASURED, NOT_MEASURED)


def test_a_known_wrong_join_is_counted_even_beside_something_unread(original, schema, keys) -> None:
    sql = """SELECT ca.ca_state FROM catalog_sales cs, customer_address ca
             WHERE cs.cs_ship_addr_sk = ca.ca_address_sk AND (cs.cs_item_sk = 1 OR cs.cs_item_sk = cs.cs_promo_sk)
             AND cs.cs_bill_customer_sk IN (SELECT c.c_customer_sk FROM customer c)"""
    got = judge(original["2"], sql, schema, keys)
    assert got.path == WRONG


def test_a_cross_join_is_path_wrong(original, schema, keys) -> None:
    sql = """SELECT s.s_store_name, SUM(ss.ss_net_paid) FROM store_sales ss, store s, date_dim d
             WHERE ss.ss_sold_date_sk = d.d_date_sk GROUP BY s.s_store_name"""
    got = judge(original["1"], sql, schema, keys)
    assert got.path == WRONG and got.tables == CORRECT


# --------------------------------------------------------------------------
# Item 66
# --------------------------------------------------------------------------

Q6_ON_CATEGORY = """
WITH s AS (SELECT i.i_category AS category, SUM(ss.ss_net_paid) AS paid
           FROM store_sales ss JOIN item i ON ss.ss_item_sk = i.i_item_sk GROUP BY i.i_category),
     c AS (SELECT i.i_category AS category, SUM(cs.cs_ext_sales_price) AS paid
           FROM catalog_sales cs JOIN item i ON cs.cs_item_sk = i.i_item_sk GROUP BY i.i_category)
SELECT s.category, s.paid, c.paid FROM s JOIN c ON s.category = c.category
"""


def test_summaries_joined_on_an_attribute_conform_and_are_marked(original, schema, keys) -> None:
    """Item 66 as recorded expected diverged. The frozen comparison reads a
    column set equal to itself as implied, so this conforms; the attribute
    join is marked beside the verdict."""
    got = judge(original["6"], Q6_ON_CATEGORY, schema, keys)
    assert (got.tables, got.path) == (CORRECT, CORRECT)
    assert got.attribute_joins == ("item.i_category = item.i_category",)


def test_summaries_joined_on_the_item_key_are_not_marked(original, schema, keys) -> None:
    sql = Q6_ON_CATEGORY.replace("i.i_category AS category", "i.i_item_sk AS category").replace(
        "GROUP BY i.i_category", "GROUP BY i.i_item_sk"
    )
    got = judge(original["6"], sql, schema, keys)
    assert got.path == CORRECT and got.attribute_joins == ()


def test_an_attribute_join_across_two_tables_is_marked_and_diverges(original, schema, keys) -> None:
    sql = """SELECT s.s_store_name, SUM(ss.ss_net_paid) FROM store_sales ss
             JOIN store s ON ss.ss_store_sk = s.s_store_sk
             JOIN date_dim d ON ss.ss_sold_date_sk = d.d_date_sk
             JOIN customer_address ca ON ca.ca_state = s.s_state GROUP BY s.s_store_name"""
    got = judge(original["1"], sql, schema, keys)
    assert got.path == WRONG and got.attribute_joins == ("customer_address.ca_state = store.s_state",)


def test_a_wrong_key_is_not_an_attribute_join(original, schema, keys) -> None:
    sql = """SELECT ca.ca_state, COUNT(*) FROM catalog_sales cs
             JOIN customer_address ca ON cs.cs_ship_addr_sk = ca.ca_address_sk GROUP BY ca.ca_state"""
    assert judge(original["2"], sql, schema, keys).attribute_joins == ()


def test_a_key_against_an_attribute_is_not_an_attribute_join(original, schema, keys) -> None:
    sql = """SELECT s.s_store_name FROM store_sales ss JOIN store s ON ss.ss_store_sk = s.s_number_employees
             JOIN date_dim d ON ss.ss_sold_date_sk = d.d_date_sk"""
    got = judge(original["1"], sql, schema, keys)
    assert got.path == WRONG and got.attribute_joins == ()


def test_key_columns_are_both_sides_of_every_foreign_key(snapshot, keys) -> None:
    assert ("item", "i_item_sk") in keys and ("store_sales", "ss_item_sk") in keys
    assert ("item", "i_category") not in keys


# --------------------------------------------------------------------------
# Retrieval recall and precision
# --------------------------------------------------------------------------


def test_recall_and_precision_against_the_tables_shown(original) -> None:
    got = retrieval_recall(original["1"], ("store_sales", "store", "store_returns", "catalog_returns"))
    assert (got.matched, got.expected, got.shown) == (2, 3, 4)
    assert got.recall == pytest.approx(2 / 3) and got.precision == pytest.approx(0.5)


def test_tables_one_of_is_one_slot_and_the_others_shown_are_surplus(original) -> None:
    got = retrieval_recall(original["8"], ("promotion", "store_sales", "catalog_sales"))
    assert (got.matched, got.expected, got.shown) == (2, 2, 3)
    got = retrieval_recall(original["8"], ("promotion",))
    assert (got.matched, got.expected) == (1, 2)


def test_a_question_expected_to_be_declined_has_no_recall(original) -> None:
    assert retrieval_recall(original["15"], ("customer",)) is None
    assert retrieval_recall(DECLINED, ()) is None


def test_nothing_shown_is_recall_zero_and_no_precision(original) -> None:
    got = retrieval_recall(original["5"], ())
    assert got.recall == 0 and got.precision is None


# --------------------------------------------------------------------------
# Runs combined
# --------------------------------------------------------------------------


def test_a_tally_counts_correct_over_measured_and_counts_the_unmeasured() -> None:
    got = tally([CORRECT, WRONG, NOT_MEASURED, CORRECT])
    assert got == Tally(2, 3, 1) and got.fraction == pytest.approx(2 / 3)
    assert tally([NOT_MEASURED]).fraction is None
    with pytest.raises(ValueError):
        tally(["agrees"])


def test_runs_combine_as_the_mean_of_fractions_with_their_range() -> None:
    got = combine([Tally(3, 4, 0), Tally(2, 4, 0), Tally(1, 2, 2), Tally(0, 0, 4)])
    assert got.runs == 4
    assert got.mean == pytest.approx((0.75 + 0.5 + 0.5) / 3)
    assert (got.low, got.high) == (0.5, 0.75)
    assert combine([Tally(0, 0, 1)]).mean is None


def test_a_question_is_stable_when_every_run_says_the_same() -> None:
    assert stability([CORRECT] * 5).stable
    assert stability([WRONG] * 5).stable
    assert stability([NOT_MEASURED] * 5).stable
    got = stability([CORRECT, CORRECT, WRONG, CORRECT, NOT_MEASURED])
    assert (got.correct, got.measured, got.runs, got.stable) == (3, 4, 5, False)
    assert not stability([CORRECT, NOT_MEASURED]).stable
