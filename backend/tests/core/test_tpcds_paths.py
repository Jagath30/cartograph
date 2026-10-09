"""PathFinder over the real TPC-DS graph, with no database.

Built from the committed DDL and overlay, so it runs in CI. The counts
below were first measured by a throwaway script that shares no code with
the PathFinder -- its own adjacency lists, its own depth-first walk -- and
are pinned here so the two can never drift apart unnoticed. They were
measured again when the overlay gained five hand-declared relationships;
that is why 11,453 paths became 13,576.

Two tests at the end are not checks of correctness. They record, as
executable facts, two things this component deliberately does not solve,
so that the step which must solve them starts from a worked example.
"""

from collections import Counter
from dataclasses import replace
from itertools import combinations

import pytest
from tpcds_files import OVERLAY, ddl_snapshot

from app.core.explainer import explain
from app.core.graph_builder import build_graph, joined_tables, table_nodes
from app.core.overlay import apply_overlay, parse_overlay
from app.core.path_finder import find_paths


@pytest.fixture(scope="module")
def graph():
    return build_graph(apply_overlay(ddl_snapshot(), parse_overlay(OVERLAY.read_text())))


@pytest.fixture(scope="module")
def every_pair(graph):
    return {pair: find_paths(graph, *pair) for pair in combinations(sorted(table_nodes(graph)), 2)}


@pytest.fixture(scope="module")
def every_pair_before():
    """The same, over the 102 relationships of tpcds_ri.sql alone: the graph
    as it was before anything was declared by hand."""
    overlay = parse_overlay(OVERLAY.read_text())
    generated = replace(overlay, relationships=tuple(r for r in overlay.relationships if not r.note))
    graph = build_graph(apply_overlay(ddl_snapshot(), generated))
    return {pair: find_paths(graph, *pair) for pair in combinations(sorted(table_nodes(graph)), 2)}


def lengths(result) -> dict[int, int]:
    return dict(sorted(Counter(path.length for path in result.discovered).items()))


def test_on_tpcds_a_fact_is_joined_to_its_dimensions_and_siblings_are_not_joined(graph) -> None:
    """The examples of the ruling itself, from the committed DDL and
    overlay: the three sales channels are not joined to one another, nor
    are the two demographics tables; a sale is joined to its store, and a
    return to its sale."""
    joined = joined_tables(graph)

    channels = ("store_sales", "catalog_sales", "web_sales")
    for one in channels:
        assert not joined[one] & set(channels)
    assert "customer_demographics" not in joined["household_demographics"]
    assert "store" in joined["store_sales"] and "store_sales" in joined["store"]
    assert "store_sales" in joined["store_returns"] and "store_returns" in joined["store_sales"]
    assert sum(len(others) for others in joined.values()) == 2 * 82


# --------------------------------------------------------------------------
# The cases the step is about
# --------------------------------------------------------------------------


def test_billing_against_shipping_is_a_tie_and_warns(graph) -> None:
    """TEST 2 on the real schema: two routes from catalog_sales to
    customer_address."""
    result = find_paths(graph, "catalog_sales", "customer_address")

    assert lengths(result) == {1: 2, 2: 4, 3: 147}
    assert [path.joins[0].fk_columns[0] for path in result.tied] == ["cs_bill_addr_sk", "cs_ship_addr_sk"]
    assert result.rule == "alphabetical"

    explanation = explain(result, graph)
    assert [warning.code for warning in explanation.warnings] == ["arbitrary_choice"]
    assert "bill address surrogate key" in explanation.warnings[0].text
    assert "ship address surrogate key" in explanation.warnings[0].text
    assert len(explanation.alternatives) == 152
    assert sum(path.tied_with_chosen for path in explanation.alternatives) == 1


def test_a_store_sale_reaches_its_address_quietly(graph) -> None:
    """TEST 3 on the real schema: 96 routes, one of them strictly shortest.
    Routes A and C of SDD figure 3 -- and a third two-join route that the
    hand-declared key opened: the sale, its return, the return's address."""
    result = find_paths(graph, "store_sales", "customer_address")

    assert lengths(result) == {1: 1, 2: 2, 3: 93}
    assert result.discovered[2].tables == ("store_sales", "store_returns", "customer_address")
    assert result.rule == "shortest"
    assert result.selected.joins[0].fk_columns == ("ss_addr_sk",)
    assert result.discovered[1].tables == ("store_sales", "customer", "customer_address")

    explanation = explain(result, graph)
    assert explanation.warnings == ()
    assert explanation.reason.text == "96 routes existed; the shortest was used."
    assert explanation.chosen.description.startswith(
        "Each store sales row has one customer address, through its address surrogate key"
    )


def test_two_fact_tables_can_only_meet_many_to_many(graph) -> None:
    """Thirteen tied routes from store_sales to catalog_sales, each through
    a shared dimension. Shortest-first has to pick one; what it must not do
    is pick one and say nothing."""
    result = find_paths(graph, "store_sales", "catalog_sales")

    assert lengths(result) == {2: 13, 3: 61}
    assert len(result.tied) == 13
    assert all(path.many_to_many_at for path in result.tied)
    assert result.selected.tables == ("store_sales", "customer", "catalog_sales")

    explanation = explain(result, graph)
    assert [warning.code for warning in explanation.warnings] == ["arbitrary_choice", "many_to_many"]
    assert explanation.warnings[1].about == ("customer",)
    assert "through customer" in explanation.warnings[1].text
    assert "rows multiply" in explanation.warnings[1].text


# --------------------------------------------------------------------------
# The whole graph, at the default limit of three joins
# --------------------------------------------------------------------------


def test_the_measured_totals(every_pair) -> None:
    assert len(every_pair) == 276
    assert sum(len(result.discovered) for result in every_pair.values()) == 13_576
    assert sum(1 for result in every_pair.values() if result.tied) == 194
    assert sum(1 for result in every_pair.values() if result.selected and result.selected.many_to_many_at) == 75


def test_the_totals_before_anything_was_declared_by_hand(every_pair_before) -> None:
    """The step 4 numbers, still reproducible from the generated 102."""
    assert sum(len(result.discovered) for result in every_pair_before.values()) == 11_453
    assert sum(1 for result in every_pair_before.values() if result.tied) == 203
    assert sum(1 for r in every_pair_before.values() if r.selected and r.selected.many_to_many_at) == 86


def test_exactly_one_pair_is_out_of_reach_at_three_joins(graph, every_pair) -> None:
    """income_band and inventory. Named, because an evaluation question
    about them would be answerable at four joins and not at three."""
    assert [pair for pair, result in every_pair.items() if result.selected is None] == [("income_band", "inventory")]

    at_four = find_paths(graph, "income_band", "inventory", max_joins=4)
    assert lengths(at_four) == {4: 33}
    assert at_four.rule == "alphabetical"
    assert all(path.many_to_many_at for path in at_four.discovered)


def test_a_higher_limit_adds_longer_routes_and_does_not_change_the_choice(graph, every_pair) -> None:
    """Shortest-first means a higher limit can only add longer routes. These
    two pairs had a single route at three joins until the returns keys were
    declared; now each has three, and still selects the same one at four."""
    for pair in [("reason", "ship_mode"), ("reason", "warehouse")]:
        at_three, at_four = every_pair[pair], find_paths(graph, *pair, max_joins=4)

        assert lengths(at_three) == {2: 1, 3: 2}
        assert (at_three.rule, at_four.rule) == ("shortest", "shortest")
        assert at_three.selected.id == at_four.selected.id


def test_every_join_found_is_a_real_foreign_key_with_its_source(graph, every_pair) -> None:
    real = {(start, end) for start, end, kind in graph.edges(data="kind") if kind == "foreign_key"}
    assert len(real) == 110

    for result in every_pair.values():
        for path in result.discovered:
            assert path.edges <= real
            assert all(join.source == "overlay" for join in path.joins)  # no catalog keys went into this graph


# --------------------------------------------------------------------------
# What the five hand-declared relationships changed
# --------------------------------------------------------------------------

RETURNS = [
    ("store_returns", "store_sales", ("sr_item_sk", "sr_ticket_number"), ("ss_item_sk", "ss_ticket_number")),
    ("catalog_returns", "catalog_sales", ("cr_item_sk", "cr_order_number"), ("cs_item_sk", "cs_order_number")),
    ("web_returns", "web_sales", ("wr_item_sk", "wr_order_number"), ("ws_item_sk", "ws_order_number")),
]


@pytest.mark.parametrize(("returns", "sales", "fk_columns", "pk_columns"), RETURNS)
def test_a_return_reaches_its_sale_in_one_join_and_nothing_warns(graph, returns, sales, fk_columns, pk_columns) -> None:
    """Before, the shortest route from a return to a sale went through a
    shared dimension: every return by a customer against every sale to that
    customer, an arbitrary choice among many and many-to-many besides. Now
    it is the return's own sale."""
    result = find_paths(graph, returns, sales)

    assert result.selected.length == 1
    assert result.rule == "shortest"
    assert result.tied == ()
    assert result.selected.many_to_many_at == ()

    [join] = result.selected.joins
    assert (join.fk_table, join.pk_table) == (returns, sales)
    assert set(zip(join.fk_columns, join.pk_columns)) == set(zip(fk_columns, pk_columns))
    assert join.source == "overlay"
    assert join.note.startswith("Not in tpcds_ri.sql")

    explanation = explain(result, graph)
    assert explanation.warnings == ()
    assert len(explanation.provenance) == 2
    assert {source for _, _, source in explanation.provenance} == {"overlay"}


def test_the_route_a_return_used_to_take_is_still_listed_and_still_marked(graph) -> None:
    result = find_paths(graph, "store_returns", "store_sales")

    assert lengths(result) == {1: 1, 2: 8, 3: 17}
    old = next(path for path in result.discovered if path.tables == ("store_returns", "customer", "store_sales"))
    assert old.many_to_many_at == ("customer",)


def test_nineteen_pairs_changed_their_path_and_none_changed_only_its_warnings(every_pair, every_pair_before) -> None:
    """Measured, not assumed. Declaring an edge can only matter to a pair by
    changing which route it selects; a pair that keeps its route keeps its
    warnings."""
    def warnings(result):
        return (result.arbitrary, bool(result.selected and result.selected.many_to_many_at))

    changed = sorted(
        pair for pair in every_pair
        if (every_pair[pair].selected and every_pair[pair].selected.id)
        != (every_pair_before[pair].selected and every_pair_before[pair].selected.id)
    )  # fmt: skip
    same_path_other_warnings = [
        pair for pair in every_pair if pair not in changed and warnings(every_pair[pair]) != warnings(every_pair_before[pair])
    ]

    assert len(changed) == 19
    assert same_path_other_warnings == []
    assert sum(1 for pair in changed if "web_page" in pair) == 9  # all by way of wp_customer_sk
    for pair in changed:
        was, now = warnings(every_pair_before[pair]), warnings(every_pair[pair])
        assert now <= was, pair  # no pair gained a warning it did not have


def test_no_selected_route_pivots_on_a_sales_table(every_pair) -> None:
    """A return points at one sale and a sale has at most one return. If the
    many-to-many rule ever fired at a sales table it would be calling that
    a fan-out. It cannot today -- nothing else references a sales table --
    and this fails the day that stops being true."""
    pivots = Counter(
        table for result in every_pair.values() if result.selected for table in result.selected.many_to_many_at
    )

    assert set(pivots) == {"customer", "date_dim"}
    assert pivots == {"customer": 20, "date_dim": 55}


def test_the_four_many_to_many_routes_through_a_hand_declared_edge_are_genuine(graph, every_pair) -> None:
    """Each pivots at customer, between a fact table and web_page: one
    customer has many sales and may own many web pages. True fan-outs."""
    by_hand = {
        edge
        for start, end, data in graph.edges(data=True)
        if data["kind"] == "foreign_key" and data["note"]
        for edge in [(start, end)]
    }
    found = sorted(
        pair for pair, result in every_pair.items()
        if result.selected and result.selected.many_to_many_at and result.selected.edges & by_hand
    )  # fmt: skip

    assert found == [
        ("catalog_returns", "web_page"),
        ("catalog_sales", "web_page"),
        ("store_returns", "web_page"),
        ("store_sales", "web_page"),
    ]
    assert all(every_pair[pair].selected.many_to_many_at == ("customer",) for pair in found)


# --------------------------------------------------------------------------
# Recorded, not solved
# --------------------------------------------------------------------------


def test_recorded_a_sale_is_said_to_have_many_returns_and_has_at_most_one(graph) -> None:
    """CARRIED FORWARD. The graph knows which side of a key holds the
    foreign key, and reads the other side as "one" and this side as "many".
    It has no way to say "at most one". Measured on this warehouse, the
    returns-side pair is unique: a sale has zero or one return.

    So the sentence below is untrue, and it is pinned here as it is so that
    whoever teaches the graph about uniqueness has a failing test to fix.
    """
    explanation = explain(find_paths(graph, "store_sales", "store_returns"), graph)

    assert explanation.chosen.joins[0].walked == "one_to_many"
    assert explanation.chosen.description.startswith("Each store sales row has many store returns rows")


def test_recorded_two_dimensions_tie_on_which_fact_bridges_them(graph) -> None:
    """CARRIED FORWARD to step 6. item and date_dim share no column, so they
    join only through a fact, and eleven fact-and-date-key combinations tie.
    Alphabetically that is catalog_returns by its returned date.

    If store_sales is also an anchor, the bridge that should win is
    store_sales by ss_sold_date_sk -- it is one of the eleven, and choosing
    any other brings a second fact table into the query for nothing. A
    PathFinder that sees one pair at a time cannot know that. Combining
    pairs into one join tree is designed where anchors first exist.
    """
    result = find_paths(graph, "item", "date_dim")

    assert lengths(result) == {2: 11, 3: 65}
    assert result.rule == "alphabetical"
    assert result.selected.tables == ("item", "catalog_returns", "date_dim")
    assert result.selected.joins[1].fk_columns == ("cr_returned_date_sk",)

    bridges = [(path.tables[1], path.joins[1].fk_columns[0]) for path in result.tied]
    assert ("store_sales", "ss_sold_date_sk") in bridges
    assert bridges.index(("store_sales", "ss_sold_date_sk")) == 7  # eighth of eleven: never chosen by the alphabet


def test_recorded_the_demonstration_tie_is_invisible_from_here(graph) -> None:
    """CARRIED FORWARD to the step that chooses anchors. "Region" reaches
    both store and customer_address, each one join from store_sales (SDD
    figure 3, routes A and B). Those are two destinations, not two routes to
    one, so each pair on its own looks settled and neither warns.

    The ambiguity is real and nothing in this component can see it. Whoever
    proposes both tables for one word has to raise it.
    """
    to_store = explain(find_paths(graph, "store_sales", "store"), graph)
    to_address = explain(find_paths(graph, "store_sales", "customer_address"), graph)

    assert to_store.chosen.length == to_address.chosen.length == 1
    assert to_store.warnings == () and to_address.warnings == ()
    assert to_store.reason.rule == to_address.reason.rule == "shortest"
