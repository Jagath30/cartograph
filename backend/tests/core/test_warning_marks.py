"""A warning set against the joins the SQL actually made (item 64; DD-21 as
amended at step 8; the owner's ruling 7).

The warnings are raised about the tree retrieval built. The SQL may use a
fraction of it. Only an arbitrary choice that touched the answer is loud.

The schema and graph are TPC-DS from the committed files; the trees are
built by the real JoinTree; every statement here was written by hand and
is read by the frozen ConformanceCheck, which is called and not changed.
"""

from dataclasses import replace

import pytest
from core.tpcds_files import OVERLAY, ddl_snapshot

from app.core.conformance import extract_joins
from app.core.explainer import explain_tree
from app.core.graph_builder import build_graph
from app.core.join_tree import build_tree
from app.core.overlay import apply_overlay, parse_overlay
from app.core.sql_reading import schema_of
from app.core.warning_marks import LOUD, Ran, had_ambiguity, mark_warnings

SNAPSHOT = apply_overlay(ddl_snapshot(), parse_overlay(OVERLAY.read_text()))
GRAPH = build_graph(SNAPSHOT)
SCHEMA = schema_of(SNAPSHOT)


def tree_of(*anchors: str):
    return build_tree(GRAPH, anchors, {anchor: 1.0 - position / 10 for position, anchor in enumerate(anchors)})


def ran(sql: str) -> Ran:
    extraction = extract_joins(sql, SCHEMA)
    return Ran(extraction.equalities, extraction.classes, bool(extraction.unchecked))


def marks(tree, sql: str | None):
    return mark_warnings(tree, explain_tree(tree, GRAPH).warnings, ran(sql) if sql is not None else None)


def key(start, end):
    """One join of one column pair, as the ConformanceCheck spells an edge."""
    return ((start, end),)


def states(tree, sql: str | None) -> list[tuple[str, str]]:
    return [(mark.code, mark.state) for mark in marks(tree, sql)]


# Billing against shipping address: one attachment, two keys, the alphabet.
ADDRESS = tree_of("catalog_sales", "customer_address")
BILL = "SELECT ca.ca_state FROM catalog_sales cs JOIN customer_address ca ON cs.cs_bill_addr_sk = ca.ca_address_sk"
SHIP = "SELECT ca.ca_state FROM catalog_sales cs JOIN customer_address ca ON cs.cs_ship_addr_sk = ca.ca_address_sk"
NO_JOIN = "SELECT COUNT(*) FROM catalog_sales"


def test_the_chosen_key_in_the_sql_is_followed_and_loud() -> None:
    (mark,) = marks(ADDRESS, BILL)
    assert (mark.code, mark.state, mark.loud) == ("arbitrary_choice", "followed", True)
    assert mark.joins == (key(("catalog_sales", "cs_bill_addr_sk"), ("customer_address", "ca_address_sk")),)
    assert mark.other_joins == (key(("catalog_sales", "cs_ship_addr_sk"), ("customer_address", "ca_address_sk")),)


def test_the_other_key_in_the_sql_is_the_other_route_taken_and_loud() -> None:
    """The choice was not obeyed, and the ambiguity touched the answer all
    the same: the reader must still be told two readings exist."""
    (mark,) = marks(ADDRESS, SHIP)
    assert (mark.state, mark.loud) == ("other_route_taken", True)


def test_a_query_that_never_makes_the_join_is_not_used_and_quiet() -> None:
    (mark,) = marks(ADDRESS, NO_JOIN)
    assert (mark.state, mark.loud) == ("not_used", False)


def test_presence_is_by_meaning_not_by_spelling() -> None:
    """The house style of TPC-DS: a comma join, the predicate in WHERE,
    written the other way round."""
    sql = "SELECT ca_state FROM customer_address, catalog_sales WHERE ca_address_sk = cs_bill_addr_sk"
    assert states(ADDRESS, sql) == [("arbitrary_choice", "followed")]


def test_both_keys_in_the_sql_reads_as_followed() -> None:
    sql = (
        "SELECT b.ca_state FROM catalog_sales cs JOIN customer_address b ON cs.cs_bill_addr_sk = b.ca_address_sk "
        "JOIN customer_address s ON cs.cs_ship_addr_sk = s.ca_address_sk"
    )
    (mark,) = marks(ADDRESS, sql)
    assert mark.loud


def test_a_join_not_found_in_sql_that_could_not_all_be_read_is_unknown_and_loud() -> None:
    """A checker that cannot read the SQL must not silence an alarm
    (T-02). The address may be joined inside the part that was not read."""
    sql = (
        "SELECT COUNT(*) FROM catalog_sales cs WHERE cs.cs_bill_addr_sk IN "
        "(SELECT ca.ca_address_sk FROM customer_address ca WHERE ca.ca_state = 'TN')"
    )
    assert ran(sql).unread
    (mark,) = marks(ADDRESS, sql)
    assert (mark.state, mark.loud) == ("unknown", True)


def test_a_join_that_was_found_is_followed_even_when_another_part_was_not_read() -> None:
    sql = BILL + " WHERE cs.cs_item_sk IN (SELECT i.i_item_sk FROM item i)"
    assert ran(sql).unread
    assert states(ADDRESS, sql) == [("arbitrary_choice", "followed")]


def test_when_no_sql_gave_an_answer_every_warning_is_not_applicable_and_quiet() -> None:
    (mark,) = marks(ADDRESS, None)
    assert (mark.state, mark.loud) == ("not_applicable", False)


def test_an_outer_join_on_the_chosen_key_is_followed() -> None:
    sql = "SELECT ca.ca_state FROM catalog_sales cs LEFT JOIN customer_address ca ON cs.cs_bill_addr_sk = ca.ca_address_sk"
    assert states(ADDRESS, sql) == [("arbitrary_choice", "followed")]


# ---- a route of two joins ---------------------------------------------------

BRIDGE = tree_of("item", "date_dim")


def test_a_tie_between_bridges_names_both_joins_of_the_chosen_route() -> None:
    (mark,) = marks(BRIDGE, "SELECT i.i_category FROM item i")
    assert mark.state == "not_used" and len(mark.joins) == 2
    assert {edge[0][0][0] for edge in mark.joins} == {"catalog_returns"}


def test_another_bridge_in_the_sql_is_the_other_route_taken() -> None:
    sql = (
        "SELECT i.i_category FROM store_sales ss JOIN item i ON ss.ss_item_sk = i.i_item_sk "
        "JOIN date_dim d ON ss.ss_sold_date_sk = d.d_date_sk"
    )
    assert states(BRIDGE, sql) == [("arbitrary_choice", "other_route_taken")]


def test_one_join_of_the_chosen_bridge_is_enough_to_have_followed_it() -> None:
    sql = "SELECT i.i_category FROM catalog_returns cr JOIN item i ON cr.cr_item_sk = i.i_item_sk"
    assert states(BRIDGE, sql) == [("arbitrary_choice", "followed")]


def test_a_join_every_tied_route_shares_decides_nothing() -> None:
    """Sold date against ship date, both through catalog_sales: the join
    from catalog_sales to item is on both routes, so making it says
    nothing about which was meant. Only the date key is the choice."""
    attachment = BRIDGE.attachments[1]
    by_id = {path.id: path for path in attachment.tied}
    sold = by_id["catalog_sales.cs_item_sk=item.i_item_sk / catalog_sales.cs_sold_date_sk=date_dim.d_date_sk"]
    ship = by_id["catalog_sales.cs_item_sk=item.i_item_sk / catalog_sales.cs_ship_date_sk=date_dim.d_date_sk"]
    tree = replace(BRIDGE, attachments=(BRIDGE.attachments[0], replace(attachment, path=sold, tied=(sold, ship))))
    warnings = tuple(w for w in explain_tree(tree, GRAPH).warnings if w.code == "arbitrary_choice")

    def state(sql: str) -> str:
        (mark,) = mark_warnings(tree, warnings, ran(sql))
        return mark.state

    (mark,) = mark_warnings(tree, warnings, None)
    assert mark.joins == (key(("catalog_sales", "cs_sold_date_sk"), ("date_dim", "d_date_sk")),)
    assert mark.other_joins == (key(("catalog_sales", "cs_ship_date_sk"), ("date_dim", "d_date_sk")),)
    shared = "SELECT i.i_category FROM catalog_sales cs JOIN item i ON cs.cs_item_sk = i.i_item_sk"
    assert state(shared) == "not_used"
    assert state(shared + " JOIN date_dim d ON cs.cs_sold_date_sk = d.d_date_sk") == "followed"
    assert state(shared + " JOIN date_dim d ON cs.cs_ship_date_sk = d.d_date_sk") == "other_route_taken"


def test_a_join_only_some_tied_routes_share_is_still_part_of_the_choice() -> None:
    """Three routes: sold date and ship date through catalog_sales, and
    one through store_sales. The join from catalog_sales to item is on two
    of the three, not on all: making it chose catalog_sales as the bridge."""
    attachment = BRIDGE.attachments[1]
    by_id = {path.id: path for path in attachment.tied}
    sold = by_id["catalog_sales.cs_item_sk=item.i_item_sk / catalog_sales.cs_sold_date_sk=date_dim.d_date_sk"]
    ship = by_id["catalog_sales.cs_item_sk=item.i_item_sk / catalog_sales.cs_ship_date_sk=date_dim.d_date_sk"]
    store = by_id["store_sales.ss_item_sk=item.i_item_sk / store_sales.ss_sold_date_sk=date_dim.d_date_sk"]
    tree = replace(BRIDGE, attachments=(BRIDGE.attachments[0], replace(attachment, path=sold, tied=(sold, ship, store))))
    warnings = tuple(w for w in explain_tree(tree, GRAPH).warnings if w.code == "arbitrary_choice")
    shared = "SELECT i.i_category FROM catalog_sales cs JOIN item i ON cs.cs_item_sk = i.i_item_sk"
    (mark,) = mark_warnings(tree, warnings, ran(shared))
    assert len(mark.joins) == 2 and mark.state == "followed"


# ---- several anchors: d7 of the development runs ---------------------------

D7 = tree_of("catalog_sales", "catalog_returns", "customer_demographics", "customer", "date_dim")
D7_SQL = (
    "SELECT date_dim.d_day_name, SUM(catalog_sales.cs_quantity) AS total_quantity FROM catalog_sales "
    "JOIN customer_demographics ON catalog_sales.cs_bill_cdemo_sk = customer_demographics.cd_demo_sk "
    "JOIN date_dim ON catalog_sales.cs_sold_date_sk = date_dim.d_date_sk "
    "WHERE customer_demographics.cd_marital_status = 'M' GROUP BY date_dim.d_day_name ORDER BY total_quantity DESC"
)


def test_d7_is_loud_about_the_joins_it_made_and_quiet_about_the_customer_it_never_joined() -> None:
    found = [(mark.code, mark.about[0].split("=")[0], mark.state) for mark in marks(D7, D7_SQL)]
    assert found == [
        ("arbitrary_choice", "catalog_sales.cs_bill_cdemo_sk", "followed"),
        ("arbitrary_choice", "catalog_sales.cs_bill_customer_sk", "not_used"),
        ("multi_anchor", "catalog_sales.cs_bill_customer_sk", "not_used"),
        # Fussy to a person, and the rule's answer: the date table could
        # equally have hung on customer. Not tuned (ruling 7).
        ("multi_anchor", "catalog_sales.cs_sold_date_sk", "followed"),
    ]


def test_a_multi_anchor_warning_is_about_the_other_place_and_not_the_other_key() -> None:
    by_key = {(mark.code, mark.about[0]): mark for mark in marks(D7, D7_SQL)}
    place = by_key["multi_anchor", "catalog_sales.cs_bill_customer_sk=customer.c_customer_sk"]
    which = by_key["arbitrary_choice", "catalog_sales.cs_bill_customer_sk=customer.c_customer_sk"]
    assert place.other_joins == (key(("customer", "c_current_cdemo_sk"), ("customer_demographics", "cd_demo_sk")),)
    assert which.other_joins == (key(("catalog_sales", "cs_ship_customer_sk"), ("customer", "c_customer_sk")),)
    # The SQL hangs customer on the other place: that warning alone turns.
    sql = D7_SQL.replace("WHERE", "JOIN customer ON customer.c_current_cdemo_sk = customer_demographics.cd_demo_sk WHERE")
    turned = {(mark.code, mark.about[0].split("=")[0]): mark.state for mark in marks(D7, sql)}
    assert turned["multi_anchor", "catalog_sales.cs_bill_customer_sk"] == "other_route_taken"
    assert turned["arbitrary_choice", "catalog_sales.cs_bill_customer_sk"] == "not_used"


# ---- many to many ----------------------------------------------------------

PIVOT = tree_of("store", "store_sales", "store_returns")
SALES_ONLY = "SELECT s.s_store_name FROM store_sales ss JOIN store s ON ss.ss_store_sk = s.s_store_sk"
BOTH_SIDES = SALES_ONLY + " JOIN store_returns sr ON sr.sr_store_sk = s.s_store_sk"


def test_rows_multiply_only_when_two_many_sides_are_joined_through_the_pivot() -> None:
    assert PIVOT.pivots == (("store", ("store_returns", "store_sales")),)
    assert dict(states(PIVOT, SALES_ONLY))["many_to_many"] == "not_used"
    assert dict(states(PIVOT, BOTH_SIDES))["many_to_many"] == "followed"
    assert dict(states(PIVOT, NO_JOIN.replace("catalog_sales", "store")))["many_to_many"] == "not_used"


def test_many_to_many_is_loud_when_it_happened_and_is_not_an_ambiguity() -> None:
    found = marks(PIVOT, SALES_ONLY + " JOIN store_returns sr ON sr.sr_store_sk = s.s_store_sk")
    pivot = next(mark for mark in found if mark.code == "many_to_many")
    assert pivot.loud and len(pivot.joins) == 2
    only_pivot = tuple(mark for mark in found if mark.code == "many_to_many")
    assert had_ambiguity(only_pivot) is False


def test_many_to_many_not_found_in_unread_sql_is_unknown() -> None:
    sql = SALES_ONLY + " WHERE ss.ss_item_sk IN (SELECT sr.sr_item_sk FROM store_returns sr)"
    assert dict(states(PIVOT, sql))["many_to_many"] == "unknown"


def test_half_of_a_two_column_key_counts_as_having_gone_that_way() -> None:
    """A return could hang on the store or on its sale, by a key of two
    columns. SQL that joins the return to the sale on the item alone went
    the other way, wrongly; the ConformanceCheck says diverged, and this
    says the other route was taken, not that the choice went unused."""
    sql = SALES_ONLY + " JOIN store_returns sr ON sr.sr_item_sk = ss.ss_item_sk"
    assert dict(states(PIVOT, sql))["multi_anchor"] == "other_route_taken"
    whole = sql + " AND sr.sr_ticket_number = ss.ss_ticket_number"
    assert dict(states(PIVOT, whole))["multi_anchor"] == "other_route_taken"


# ---- the extracted column --------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "expected"),
    [(BILL, True), (SHIP, True), (NO_JOIN, False), (None, False)],
)
def test_had_ambiguity_is_an_arbitrary_choice_that_touched_the_answer(sql, expected) -> None:
    """Ruling 4: the loud states count, of arbitrary_choice and multi_anchor."""
    assert had_ambiguity(marks(ADDRESS, sql)) is expected


def test_unknown_counts_as_ambiguity() -> None:
    sql = "SELECT COUNT(*) FROM catalog_sales cs WHERE cs.cs_bill_addr_sk IN (SELECT ca.ca_address_sk FROM customer_address ca)"
    assert had_ambiguity(marks(ADDRESS, sql)) is True


def test_the_loud_states_are_exactly_three() -> None:
    assert LOUD == {"followed", "other_route_taken", "unknown"}


# ---- nothing is lost or reordered ------------------------------------------


def test_every_warning_is_marked_once_in_the_explainers_order_with_its_words() -> None:
    for tree in (ADDRESS, BRIDGE, D7, PIVOT, tree_of("store_sales", "store")):
        warnings = explain_tree(tree, GRAPH).warnings
        found = mark_warnings(tree, warnings, None)
        assert [(m.code, m.text, m.about) for m in found] == [(w.code, w.text, w.about) for w in warnings]


def test_a_declined_tree_marks_its_no_path_warning_not_applicable() -> None:
    tree = build_tree(GRAPH, ("income_band", "ship_mode"), {"income_band": 1.0, "ship_mode": 0.9}, max_joins=1)
    assert tree.declined
    (mark,) = mark_warnings(tree, explain_tree(tree, GRAPH).warnings, None)
    assert (mark.code, mark.state, mark.joins) == ("no_path", "not_applicable", ())


def test_warnings_that_do_not_belong_to_the_tree_are_refused() -> None:
    """The marks are worked out from the tree; a list of warnings from
    another tree would be marked against the wrong joins."""
    with pytest.raises(ValueError):
        mark_warnings(ADDRESS, explain_tree(D7, GRAPH).warnings, None)


def test_a_loud_multi_anchor_alone_is_an_ambiguity() -> None:
    only = tuple(mark for mark in marks(D7, D7_SQL) if mark.code == "multi_anchor")
    assert [mark.loud for mark in only] == [False, True]
    assert had_ambiguity(only) is True
    assert had_ambiguity(only[:1]) is False
