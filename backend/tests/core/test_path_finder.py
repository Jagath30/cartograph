"""PathFinder and Explainer, from the hand-written fixture (FR-11 to FR-14,
FR-40, FR-45, NFR-26). No database.

The fixture in conftest.py is small enough to enumerate by eye:

    store_sales --ss_addr_sk-----> customer_address        route A
    store_sales --ss_store_sk----> store                   route B
    store_sales --ss_customer_sk-> customer --c_current_addr_sk-> customer_address   route C
    catalog_sales --cs_bill_addr_sk-> customer_address     (catalog)
    catalog_sales --cs_ship_addr_sk-> customer_address     (overlay)

The four tests the step was asked for are marked TEST 1 to TEST 4. The rest
are the negative cases: what must not be found, must not be silenced, and
must not be chosen.
"""

from dataclasses import replace

import pytest

from app.core.explainer import explain
from app.core.graph_builder import build_graph
from app.core.overlay import apply_overlay, parse_overlay
from app.core.path_finder import DEFAULT_MAX_JOINS, MANY_TO_ONE, ONE_TO_MANY, find_paths
from app.core.snapshot import Column, ForeignKey, Preference, SchemaSnapshot, Table

BILL = ("catalog_sales.cs_bill_addr_sk", "customer_address.ca_address_sk")
SHIP = ("catalog_sales.cs_ship_addr_sk", "customer_address.ca_address_sk")

NAMING = """
naming:
  prefixes: { cs_: catalog sales, ss_: store sales, c_: customer, ca_: customer address, s_: store }
  words:    { sk: surrogate key, addr: address }
"""


@pytest.fixture
def graph(small_snapshot):
    return build_graph(apply_overlay(small_snapshot, parse_overlay(NAMING)))


def with_preference(snapshot, *edges, between=("catalog_sales", "customer_address"), because="billing is what counts"):
    preference = Preference(between=between, prefer=tuple(edges), because=because)
    return build_graph(replace(apply_overlay(snapshot, parse_overlay(NAMING)), preferences=(preference,)))


def codes(explanation) -> list[str]:
    return [warning.code for warning in explanation.warnings]


# --------------------------------------------------------------------------
# TEST 1 -- pure path finding from the fixture
# --------------------------------------------------------------------------


def test_one_join_is_one_hop_although_it_is_three_edges_in_the_graph(graph) -> None:
    result = find_paths(graph, "store_sales", "store")

    assert len(result.discovered) == 1
    path = result.selected
    assert path.length == 1
    assert path.tables == ("store_sales", "store")
    assert result.rule == "only_path"
    assert not result.tied and not result.arbitrary


def test_every_route_is_found_shortest_first(graph) -> None:
    """Routes A and C of the demonstration: same destination, different
    meaning, different length."""
    result = find_paths(graph, "store_sales", "customer_address")

    assert [path.tables for path in result.discovered] == [
        ("store_sales", "customer_address"),
        ("store_sales", "customer", "customer_address"),
    ]
    assert [join.fk_columns for join in result.discovered[1].joins] == [("ss_customer_sk",), ("c_current_addr_sk",)]


def test_the_search_walks_a_foreign_key_from_either_end(graph) -> None:
    """customer_address holds no foreign keys at all, so every route out of
    it is walked against the arrows."""
    result = find_paths(graph, "customer_address", "store")

    assert result.selected.tables == ("customer_address", "store_sales", "store")
    assert [join.walked for join in result.selected.joins] == [ONE_TO_MANY, MANY_TO_ONE]


def test_a_join_keeps_its_real_direction_whichever_way_it_was_walked(graph) -> None:
    forwards = find_paths(graph, "store_sales", "store").selected.joins[0]
    backwards = find_paths(graph, "store", "store_sales").selected.joins[0]

    for join in (forwards, backwards):
        assert (join.fk_table, join.fk_columns) == ("store_sales", ("ss_store_sk",))
        assert (join.pk_table, join.pk_columns) == ("store", ("s_store_sk",))
        assert join.edges == (("store_sales.ss_store_sk", "store.s_store_sk"),)
    assert (forwards.walked, forwards.from_table, forwards.to_table) == (MANY_TO_ONE, "store_sales", "store")
    assert (backwards.walked, backwards.from_table, backwards.to_table) == (ONE_TO_MANY, "store", "store_sales")


def test_no_path_visits_a_table_twice(graph) -> None:
    for start, end in [("store", "catalog_sales"), ("customer", "catalog_sales"), ("store_sales", "customer_address")]:
        for path in find_paths(graph, start, end, max_joins=4).discovered:
            assert len(set(path.tables)) == len(path.tables)


def test_the_limit_is_three_joins_and_counts_joins_not_edges(graph) -> None:
    """store reaches catalog_sales in three joins, or in four by way of
    customer. The default finds the first kind only."""
    assert DEFAULT_MAX_JOINS == 3

    by_default = find_paths(graph, "store", "catalog_sales")
    assert {path.length for path in by_default.discovered} == {3}
    assert len(by_default.discovered) == 2

    at_four = find_paths(graph, "store", "catalog_sales", max_joins=4)
    assert sorted(path.length for path in at_four.discovered) == [3, 3, 4, 4]

    assert find_paths(graph, "store_sales", "customer_address", max_joins=1).rule == "only_path"


def test_asking_from_the_other_end_finds_and_chooses_the_same_routes(graph) -> None:
    for start, end in [("catalog_sales", "customer_address"), ("store", "catalog_sales"), ("store", "customer")]:
        one_way = find_paths(graph, start, end)
        other_way = find_paths(graph, end, start)

        assert sorted(path.id for path in one_way.discovered) == sorted(path.id for path in other_way.discovered)
        assert one_way.selected.id == other_way.selected.id
        assert one_way.selected.tables == tuple(reversed(other_way.selected.tables))
        assert one_way.rule == other_way.rule


def test_what_is_not_a_pair_of_tables_is_refused(graph) -> None:
    with pytest.raises(ValueError, match="not a table"):
        find_paths(graph, "store_sales", "shop")
    with pytest.raises(ValueError, match="not a table"):
        find_paths(graph, "store_sales.ss_store_sk", "store")
    with pytest.raises(ValueError, match="two different tables"):
        find_paths(graph, "store", "store")
    with pytest.raises(ValueError, match="at least 1"):
        find_paths(graph, "store", "store_sales", max_joins=0)


def test_a_composite_key_is_one_join_not_two_parallel_ones() -> None:
    snapshot = SchemaSnapshot(
        tables=(Table("line"), Table("order_header")),
        columns=tuple(
            Column(table, name, "bigint")
            for table, name in [("line", "shop"), ("line", "number"), ("order_header", "shop"), ("order_header", "number")]
        ),
        primary_keys=(),
        foreign_keys=(ForeignKey("line", ("shop", "number"), "order_header", ("shop", "number"), "catalog", "line_fk"),),
    )
    graph = build_graph(apply_overlay(snapshot, parse_overlay("")))
    result = find_paths(graph, "line", "order_header")

    assert len(result.discovered) == 1
    assert result.selected.joins[0].fk_columns == ("shop", "number")
    assert explain(result, graph).chosen.description == "Each line row has one order header, through its shop and number."


# --------------------------------------------------------------------------
# TEST 2 -- a real tie: detected, and the warning fires
# --------------------------------------------------------------------------


def test_two_routes_to_the_same_table_are_a_tie_and_the_tie_warns(graph) -> None:
    """Billing address against shipping address. Both one join, both valid,
    and nothing in the schema says which a question means (DD-21)."""
    result = find_paths(graph, "catalog_sales", "customer_address")

    assert [path.joins[0].fk_columns for path in result.tied] == [("cs_bill_addr_sk",), ("cs_ship_addr_sk",)]
    assert result.rule == "alphabetical"
    assert result.arbitrary is True
    assert result.selected.joins[0].fk_columns == ("cs_bill_addr_sk",)  # b before s, and for no better reason

    explanation = explain(result, graph)
    assert codes(explanation) == ["arbitrary_choice"]
    assert explanation.reason.rule == "alphabetical"
    assert "arbitrary" in explanation.reason.text


def test_the_route_not_taken_is_named_in_the_warning_itself(graph) -> None:
    """The silent failure of step 4: a path that is valid, short, and not
    the one meant. It is not enough for the ship route to sit in a list of
    alternatives -- the warning has to say it, in words."""
    explanation = explain(find_paths(graph, "catalog_sales", "customer_address"), graph)
    warning = explanation.warnings[0]

    assert "I had no basis for this choice" in warning.text
    assert "bill address surrogate key" in warning.text
    assert "Equally valid" in warning.text
    assert "ship address surrogate key" in warning.text
    assert len(warning.about) == 2

    [ship] = explanation.alternatives
    assert ship.tied_with_chosen is True
    assert ship.joins[0].fk_columns == ("cs_ship_addr_sk",)


def test_the_tie_break_reads_columns_where_table_names_cannot_decide(graph) -> None:
    """DD-12 says "alphabetically by the sequence of table names". Both
    routes here have the same sequence, so that rule alone decides nothing."""
    result = find_paths(graph, "catalog_sales", "customer_address")

    assert result.tied[0].tables == result.tied[1].tables
    assert [path.joins[0].fk_columns[0] for path in result.discovered] == ["cs_bill_addr_sk", "cs_ship_addr_sk"]


def test_the_choice_does_not_depend_on_the_order_things_were_declared_in(small_snapshot) -> None:
    """Why DD-12 chose alphabetical: "declaration order can change between
    ingestions and alphabetical cannot". The same schema with its columns
    and foreign keys listed backwards must find the same routes in the same
    order and select the same one.

    Both are reversed because the graph's edge order follows the order its
    column nodes were added, not the order the keys were. Without column
    names in the tie-break, the ship key -- now declared first -- would win.
    """
    snapshot = apply_overlay(small_snapshot, parse_overlay(NAMING))
    backwards = replace(
        snapshot,
        columns=tuple(reversed(snapshot.columns)),
        foreign_keys=tuple(reversed(snapshot.foreign_keys)),
    )

    for start, end in [("catalog_sales", "customer_address"), ("store", "catalog_sales"), ("customer", "catalog_sales")]:
        one = find_paths(build_graph(snapshot), start, end)
        other = find_paths(build_graph(backwards), start, end)

        assert [path.id for path in one.discovered] == [path.id for path in other.discovered]
        assert one.selected.id == other.selected.id

    selected = find_paths(build_graph(backwards), "catalog_sales", "customer_address").selected
    assert selected.joins[0].fk_columns == ("cs_bill_addr_sk",)


# --------------------------------------------------------------------------
# TEST 3 -- a non-tie: alternatives exist, one is strictly shorter, no warning
# --------------------------------------------------------------------------


def test_a_strictly_shorter_route_wins_quietly(graph) -> None:
    result = find_paths(graph, "store_sales", "customer_address")

    assert len(result.discovered) == 2
    assert result.rule == "shortest"
    assert result.tied == ()
    assert result.arbitrary is False

    explanation = explain(result, graph)
    assert explanation.warnings == ()
    assert explanation.reason.text == "2 routes existed; the shortest was used."


def test_the_longer_route_is_still_reported_as_an_alternative(graph) -> None:
    """Quiet is not the same as hidden: route C is listed, described, and
    marked as not tied."""
    explanation = explain(find_paths(graph, "store_sales", "customer_address"), graph)

    [longer] = explanation.alternatives
    assert longer.tables == ("store_sales", "customer", "customer_address")
    assert longer.tied_with_chosen is False
    assert longer.description == (
        "Each store sales row has one customer, through its customer surrogate key; "
        "then each customer row has one customer address, through its current address surrogate key."
    )


def test_a_single_route_says_so_and_does_not_warn(graph) -> None:
    explanation = explain(find_paths(graph, "store_sales", "store"), graph)

    assert explanation.warnings == ()
    assert explanation.alternatives == ()
    assert explanation.reason.text == "Only one route of 3 joins or fewer connects store sales and store."


# --------------------------------------------------------------------------
# TEST 4 -- an explanation names the provenance of every edge it used
# --------------------------------------------------------------------------


def test_every_edge_of_the_chosen_path_carries_its_source(graph) -> None:
    explanation = explain(find_paths(graph, "store", "customer"), graph)

    assert explanation.chosen.tables == ("store", "store_sales", "customer")
    assert explanation.provenance == (
        ("store_sales.ss_store_sk", "store.s_store_sk", "catalog"),
        ("store_sales.ss_customer_sk", "customer.c_customer_sk", "catalog"),
    )
    assert [(join.source, join.constraint) for join in explanation.chosen.joins] == [
        ("catalog", "ss_store_sk_fk"),
        ("catalog", "ss_customer_sk_fk"),
    ]


def test_an_overlay_edge_is_said_to_be_one_in_the_data_and_in_the_sentence(small_snapshot) -> None:
    """The ship key is the fixture's one overlay edge. Chosen by a declared
    preference, it must be visibly a thing a human asserted (DD-16)."""
    graph = with_preference(small_snapshot, SHIP)
    explanation = explain(find_paths(graph, "catalog_sales", "customer_address"), graph)

    assert explanation.provenance == ((*SHIP, "overlay"),)
    assert explanation.chosen.joins[0].constraint is None
    assert explanation.chosen.description == (
        "Each catalog sales row has one customer address, through its ship address surrogate key "
        "(asserted in the overlay, not declared by the database)."
    )


def test_alternatives_carry_their_provenance_too(graph) -> None:
    explanation = explain(find_paths(graph, "catalog_sales", "customer_address"), graph)

    assert explanation.chosen.joins[0].source == "catalog"
    assert "overlay" not in explanation.chosen.description
    assert explanation.alternatives[0].joins[0].source == "overlay"
    assert "asserted in the overlay" in explanation.alternatives[0].description


def test_the_sentence_says_which_side_has_one_row_and_which_has_many(graph) -> None:
    explanation = explain(find_paths(graph, "customer_address", "store"), graph)

    assert explanation.chosen.description == (
        "Each customer address row has many store sales rows, through their address surrogate key; "
        "then each store sales row has one store, through its store surrogate key."
    )


def test_without_readable_names_the_sentence_falls_back_to_identifiers(small_snapshot) -> None:
    bare = build_graph(small_snapshot)

    assert explain(find_paths(bare, "store_sales", "store"), bare).chosen.description == (
        "Each store_sales row has one store, through its ss_store_sk."
    )


# --------------------------------------------------------------------------
# Preferences: what resolves a tie, and what must never silence the warning
# --------------------------------------------------------------------------


def test_a_declared_preference_resolves_the_tie_and_nothing_warns(small_snapshot) -> None:
    graph = with_preference(small_snapshot, SHIP, because="goods are counted where they arrive")
    result = find_paths(graph, "catalog_sales", "customer_address")

    assert result.rule == "preference"
    assert result.arbitrary is False
    assert result.selected.joins[0].fk_columns == ("cs_ship_addr_sk",)  # not the alphabetical winner
    assert len(result.tied) == 2  # the tie existed, and is still on record

    explanation = explain(result, graph)
    assert explanation.warnings == ()
    assert explanation.reason.text == (
        "2 routes of 1 join connect catalog sales and customer address. "
        "The overlay declares which is meant, because: goods are counted where they arrive."
    )
    assert explanation.alternatives[0].tied_with_chosen is True


def test_a_preference_read_from_overlay_text_works_end_to_end(small_snapshot) -> None:
    text = NAMING + """
preferences:
  - between: [customer_address, catalog_sales]
    prefer:
      - from: catalog_sales.cs_ship_addr_sk
        to:   customer_address.ca_address_sk
    because: goods are counted
             where they arrive
"""
    graph = build_graph(apply_overlay(small_snapshot, parse_overlay(text)))
    result = find_paths(graph, "customer_address", "catalog_sales")

    assert result.rule == "preference"
    assert result.preference_applied.between == ("catalog_sales", "customer_address")
    assert result.preference_applied.because == "goods are counted where they arrive"


def test_a_preference_that_names_none_of_the_tied_routes_silences_nothing(small_snapshot) -> None:
    """A stale preference. catalog_sales gains a route through customer, two
    joins long, and the overlay prefers it. The tie is between the two
    one-join routes, so the preference decides nothing -- and the arbitrary
    warning must fire exactly as if it were not there."""
    extended = replace(
        small_snapshot,
        columns=small_snapshot.columns + (Column("catalog_sales", "cs_bill_customer_sk", "bigint"),),
        foreign_keys=small_snapshot.foreign_keys
        + (ForeignKey("catalog_sales", ("cs_bill_customer_sk",), "customer", ("c_customer_sk",), "catalog", "x"),),
    )
    graph = with_preference(
        extended,
        ("catalog_sales.cs_bill_customer_sk", "customer.c_customer_sk"),
        ("customer.c_current_addr_sk", "customer_address.ca_address_sk"),
    )
    result = find_paths(graph, "catalog_sales", "customer_address")

    assert result.rule == "alphabetical"
    assert result.arbitrary is True
    assert result.preference_applied is None
    assert result.selected.joins[0].fk_columns == ("cs_bill_addr_sk",)

    explanation = explain(result, graph)
    assert codes(explanation) == ["arbitrary_choice", "preference_not_applied"]
    assert "does not name one of the tied routes" in explanation.warnings[1].text


def test_a_preference_cannot_promote_a_longer_route_over_a_shorter_one(small_snapshot) -> None:
    """DD-12 lists the preference after "shortest wins". Route C preferred,
    route A one join shorter: A is used, and the unused preference is said."""
    graph = with_preference(
        small_snapshot,
        ("store_sales.ss_customer_sk", "customer.c_customer_sk"),
        ("customer.c_current_addr_sk", "customer_address.ca_address_sk"),
        between=("customer_address", "store_sales"),
    )
    result = find_paths(graph, "store_sales", "customer_address")

    assert result.rule == "shortest"
    assert result.selected.length == 1

    explanation = explain(result, graph)
    assert codes(explanation) == ["preference_not_applied"]
    assert "a preference only breaks ties" in explanation.warnings[0].text


def test_a_preference_for_the_route_that_wins_anyway_is_simply_satisfied(small_snapshot) -> None:
    graph = with_preference(
        small_snapshot,
        ("store_sales.ss_addr_sk", "customer_address.ca_address_sk"),
        between=("customer_address", "store_sales"),
    )
    result = find_paths(graph, "store_sales", "customer_address")

    assert result.rule == "shortest"
    assert explain(result, graph).warnings == ()


def test_a_preference_for_another_pair_changes_nothing(small_snapshot) -> None:
    graph = with_preference(small_snapshot, BILL)

    assert find_paths(graph, "store_sales", "customer_address").rule == "shortest"
    assert find_paths(graph, "customer", "catalog_sales").rule == "alphabetical"


# --------------------------------------------------------------------------
# Shape: reported, warned about, never ranked on
# --------------------------------------------------------------------------


def test_a_fact_bridging_two_dimensions_is_not_many_to_many(graph) -> None:
    """store <- store_sales -> customer_address. Each sale has one store and
    one address: an ordinary star join."""
    result = find_paths(graph, "store", "customer_address")

    assert result.selected.tables == ("store", "store_sales", "customer_address")
    assert result.selected.many_to_many_at == ()
    assert explain(result, graph).warnings == ()


def test_two_many_sides_meeting_at_one_table_are_found_and_named(graph) -> None:
    """store_sales -> customer_address <- catalog_sales: every store sale at
    an address paired with every catalog sale at it."""
    result = find_paths(graph, "store", "catalog_sales")

    assert result.selected.tables == ("store", "store_sales", "customer_address", "catalog_sales")
    assert result.selected.many_to_many_at == ("customer_address",)

    explanation = explain(result, graph)
    assert codes(explanation) == ["arbitrary_choice", "many_to_many"]
    warning = explanation.warnings[1]
    assert warning.about == ("customer_address",)
    assert warning.text == (
        "This route joins store sales to catalog sales through customer address. One customer address row has "
        "many store sales rows and many catalog sales rows, so every store sales row is paired with every "
        "catalog sales row that shares it: rows multiply, and sums and counts over them are inflated."
    )


def test_shape_does_not_change_the_ranking(graph) -> None:
    """Charter D-06: no heuristic favours one kind of table. A many-to-many
    route that is shortest is still selected -- and said to be what it is."""
    result = find_paths(graph, "customer", "catalog_sales")

    assert result.selected.length == 2
    assert result.selected.many_to_many_at == ("customer_address",)
    assert [path.length for path in result.discovered] == [2, 2, 3, 3]
    assert all(path.many_to_many_at for path in result.discovered)


# --------------------------------------------------------------------------
# No route: reported, not repaired (DD-10)
# --------------------------------------------------------------------------


def test_when_nothing_connects_within_the_limit_the_result_says_so(graph) -> None:
    result = find_paths(graph, "store", "catalog_sales", max_joins=2)

    assert result.discovered == ()
    assert result.selected is None
    assert result.rule is None

    explanation = explain(result, graph)
    assert explanation.chosen is None
    assert explanation.provenance == ()
    assert codes(explanation) == ["no_path"]
    assert explanation.reason.text == "No route of 2 joins or fewer connects store and catalog sales."


def test_an_isolated_table_has_no_route_and_does_not_raise(small_snapshot) -> None:
    alone = build_graph(replace(small_snapshot, tables=small_snapshot.tables + (Table("reason"),)))

    assert find_paths(alone, "reason", "store").selected is None
