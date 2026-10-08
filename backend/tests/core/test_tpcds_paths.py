"""PathFinder over the real TPC-DS graph, with no database.

Built from the committed DDL and overlay, so it runs in CI. The counts
below were first measured by a throwaway script that shares no code with
the PathFinder -- its own adjacency lists, its own depth-first walk -- and
are pinned here so the two can never drift apart unnoticed.

Two tests at the end are not checks of correctness. They record, as
executable facts, two things this component deliberately does not solve,
so that the step which must solve them starts from a worked example.
"""

from collections import Counter
from itertools import combinations

import pytest
from tpcds_files import OVERLAY, ddl_snapshot

from app.core.explainer import explain
from app.core.graph_builder import build_graph, table_nodes
from app.core.overlay import apply_overlay, parse_overlay
from app.core.path_finder import find_paths


@pytest.fixture(scope="module")
def graph():
    return build_graph(apply_overlay(ddl_snapshot(), parse_overlay(OVERLAY.read_text())))


@pytest.fixture(scope="module")
def every_pair(graph):
    return {pair: find_paths(graph, *pair) for pair in combinations(sorted(table_nodes(graph)), 2)}


def lengths(result) -> dict[int, int]:
    return dict(sorted(Counter(path.length for path in result.discovered).items()))


# --------------------------------------------------------------------------
# The cases the step is about
# --------------------------------------------------------------------------


def test_billing_against_shipping_is_a_tie_and_warns(graph) -> None:
    """TEST 2 on the real schema: two routes from catalog_sales to
    customer_address."""
    result = find_paths(graph, "catalog_sales", "customer_address")

    assert lengths(result) == {1: 2, 2: 2, 3: 143}
    assert [path.joins[0].fk_columns[0] for path in result.tied] == ["cs_bill_addr_sk", "cs_ship_addr_sk"]
    assert result.rule == "alphabetical"

    explanation = explain(result, graph)
    assert [warning.code for warning in explanation.warnings] == ["arbitrary_choice"]
    assert "bill address surrogate key" in explanation.warnings[0].text
    assert "ship address surrogate key" in explanation.warnings[0].text
    assert len(explanation.alternatives) == 146
    assert sum(path.tied_with_chosen for path in explanation.alternatives) == 1


def test_a_store_sale_reaches_its_address_quietly(graph) -> None:
    """TEST 3 on the real schema: 93 routes, one of them strictly shortest.
    Routes A and C of SDD figure 3."""
    result = find_paths(graph, "store_sales", "customer_address")

    assert lengths(result) == {1: 1, 2: 1, 3: 91}
    assert result.rule == "shortest"
    assert result.selected.joins[0].fk_columns == ("ss_addr_sk",)
    assert result.discovered[1].tables == ("store_sales", "customer", "customer_address")

    explanation = explain(result, graph)
    assert explanation.warnings == ()
    assert explanation.reason.text == "93 routes existed; the shortest was used."
    assert explanation.chosen.description.startswith(
        "Each store sales row has one customer address, through its address surrogate key"
    )


def test_two_fact_tables_can_only_meet_many_to_many(graph) -> None:
    """Thirteen tied routes from store_sales to catalog_sales, each through
    a shared dimension. Shortest-first has to pick one; what it must not do
    is pick one and say nothing."""
    result = find_paths(graph, "store_sales", "catalog_sales")

    assert lengths(result) == {2: 13, 3: 34}
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
    assert sum(len(result.discovered) for result in every_pair.values()) == 11_453
    assert sum(1 for result in every_pair.values() if result.tied) == 203
    assert sum(1 for result in every_pair.values() if result.selected and result.selected.many_to_many_at) == 86


def test_exactly_one_pair_is_out_of_reach_at_three_joins(graph, every_pair) -> None:
    """income_band and inventory. Named, because an evaluation question
    about them would be answerable at four joins and not at three."""
    assert [pair for pair, result in every_pair.items() if result.selected is None] == [("income_band", "inventory")]

    at_four = find_paths(graph, "income_band", "inventory", max_joins=4)
    assert lengths(at_four) == {4: 32}
    assert at_four.rule == "alphabetical"
    assert all(path.many_to_many_at for path in at_four.discovered)


def test_at_four_joins_two_more_pairs_change_their_reason_but_not_their_path(graph, every_pair) -> None:
    """Shortest-first means a higher limit can only add longer routes. For
    these two that turns "the only route" into "the shortest of several";
    the path selected is the same."""
    for pair in [("reason", "ship_mode"), ("reason", "warehouse")]:
        at_three, at_four = every_pair[pair], find_paths(graph, *pair, max_joins=4)

        assert (at_three.rule, at_four.rule) == ("only_path", "shortest")
        assert at_three.selected.id == at_four.selected.id


def test_every_join_found_is_a_real_foreign_key_with_its_source(graph, every_pair) -> None:
    real = {(start, end) for start, end, kind in graph.edges(data="kind") if kind == "foreign_key"}
    assert len(real) == 102

    for result in every_pair.values():
        for path in result.discovered:
            assert path.edges <= real
            assert all(join.source == "overlay" for join in path.joins)  # no catalog keys went into this graph


# --------------------------------------------------------------------------
# Recorded, not solved
# --------------------------------------------------------------------------


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

    assert lengths(result) == {2: 11, 3: 47}
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
