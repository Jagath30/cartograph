"""The join tree: several anchors, one tree (FR-10, FR-11, DD-10, DD-11;
ruling b of step 6).

Two graphs, neither needing a database:

  the miniature   the hand-written fixture of conftest.py, with one more
                  table added where a test needs a second bridge.
  TPC-DS          the committed DDL and overlay, for the two shapes
                  CHECKPOINTS.md records: the bridge that pairs get wrong
                  (item 32), and the place where a tree has no basis to
                  choose (the risk the owner accepted for ruling b).

No score below came from an embedding, and no test reads the evaluation set.
"""

from dataclasses import replace

import pytest
from tpcds_files import OVERLAY, ddl_snapshot

from app.core.explainer import explain_tree, route_codes
from app.core.graph_builder import build_graph
from app.core.join_tree import ANCHORS_NOT_CONNECTED, NO_ANCHORS, SetAside, build_tree
from app.core.overlay import apply_overlay, parse_overlay
from app.core.path_finder import find_paths
from app.core.snapshot import Column, ForeignKey, Preference, Table

NAMING = """
naming:
  prefixes: { cs_: catalog sales, ss_: store sales, c_: customer, ca_: customer address, s_: store, ws_: web sales }
  words:    { sk: surrogate key, addr: address }
"""


@pytest.fixture
def graph(small_snapshot):
    return build_graph(apply_overlay(small_snapshot, parse_overlay(NAMING)))


@pytest.fixture
def with_web_sales(small_snapshot):
    """A second table bridging store and customer_address."""
    return replace(
        small_snapshot,
        tables=small_snapshot.tables + (Table("web_sales"),),
        columns=small_snapshot.columns
        + (Column("web_sales", "ws_addr_sk", "bigint"), Column("web_sales", "ws_store_sk", "bigint")),
        foreign_keys=small_snapshot.foreign_keys
        + (
            ForeignKey("web_sales", ("ws_addr_sk",), "customer_address", ("ca_address_sk",), "catalog", "a_fk"),
            ForeignKey("web_sales", ("ws_store_sk",), "store", ("s_store_sk",), "catalog", "b_fk"),
        ),
    )


@pytest.fixture(scope="module")
def tpcds():
    return build_graph(apply_overlay(ddl_snapshot(), parse_overlay(OVERLAY.read_text())))


def _scores(*anchors: str) -> dict[str, float]:
    """Best first: 1.0, 0.9, 0.8, ..."""
    return {anchor: 1.0 - position / 10 for position, anchor in enumerate(anchors)}


def _tree(graph, *anchors: str, **options):
    return build_tree(graph, anchors, _scores(*anchors), **options)


def _attached(tree) -> list[tuple[str, str | None]]:
    return [(attachment.anchor, attachment.attached_to) for attachment in tree.attachments]


def _joined(tree) -> set[str]:
    return {f"{start}={end}" for join in tree.joins for start, end in join.edges}


# --------------------------------------------------------------------------
# The seed and the order (ruling b)
# --------------------------------------------------------------------------


def test_the_seed_is_the_highest_scoring_anchor_whatever_order_they_came_in(graph) -> None:
    scores = {"store": 0.4, "store_sales": 0.9, "customer": 0.6}
    tree = build_tree(graph, ("store", "customer", "store_sales"), scores)
    assert tree.seed == "store_sales"
    assert tree.attachments[0].anchor == "store_sales"
    assert (tree.attachments[0].order, tree.attachments[0].path) == (0, None)


def test_equal_scores_seed_alphabetically(graph) -> None:
    scores = {"store": 0.5, "customer": 0.5, "store_sales": 0.5}
    assert build_tree(graph, ("store_sales", "store", "customer"), scores).seed == "customer"


def test_the_order_of_attachment_is_recorded(graph) -> None:
    tree = _tree(graph, "store", "customer", "store_sales")
    assert [(attachment.order, attachment.anchor) for attachment in tree.attachments] == [
        (0, "store"),
        (1, "store_sales"),
        (2, "customer"),
    ]


def test_the_nearest_anchor_is_attached_first_not_the_next_best_scoring(graph) -> None:
    """customer scores above store_sales but is two joins from store;
    store_sales is one. Nearest first: then customer is one join from the
    tree and needs no bridge of its own."""
    tree = _tree(graph, "store", "customer", "store_sales")
    assert _attached(tree) == [("store", None), ("store_sales", "store"), ("customer", "store_sales")]
    assert len(tree.joins) == 2


def test_among_equally_near_anchors_the_higher_score_goes_first(graph) -> None:
    tree = _tree(graph, "store_sales", "store", "customer_address")
    assert [attachment.anchor for attachment in tree.attachments] == ["store_sales", "store", "customer_address"]
    swapped = build_tree(
        graph, ("store_sales", "store", "customer_address"), {"store_sales": 1.0, "store": 0.5, "customer_address": 0.8}
    )
    assert [attachment.anchor for attachment in swapped.attachments] == ["store_sales", "customer_address", "store"]


# --------------------------------------------------------------------------
# Two anchors: the PathFinder's own answer
# --------------------------------------------------------------------------


def test_with_two_anchors_the_tree_is_the_pathfinders_answer(graph) -> None:
    for pair in (("catalog_sales", "customer_address"), ("store_sales", "customer_address"), ("store", "customer")):
        tree = _tree(graph, *pair)
        result = find_paths(graph, pair[1], pair[0])
        (_, attachment) = tree.attachments
        assert tree.edges == frozenset(frozenset(join.edges) for join in result.selected.joins)
        assert attachment.rule == result.rule
        assert {path.id for path in attachment.tied} == {path.id for path in result.tied}


def test_a_tie_between_two_keys_to_the_same_table_is_arbitrary_choice_and_not_multi_anchor(graph) -> None:
    tree = _tree(graph, "catalog_sales", "customer_address")
    explanation = explain_tree(tree, graph)
    assert explanation.codes == {"arbitrary_choice"}
    (warning,) = explanation.warnings
    assert "I had no basis for this choice" in warning.text
    assert "bill address surrogate key" in warning.text and "ship address surrogate key" in warning.text


def test_the_questions_wording_decides_between_two_keys_to_one_table_inside_a_tree(graph) -> None:
    """The evidence must reach the PathFinder through the tree. (With it
    not passed down, every other test here still passed.)"""
    evidence = {"catalog_sales.cs_bill_addr_sk": 0.25, "catalog_sales.cs_ship_addr_sk": 0.75}
    tree = _tree(graph, "catalog_sales", "customer_address", evidence=evidence, margin=0.25)

    assert tree.attachments[1].rule == "question_evidence"
    assert _joined(tree) == {"catalog_sales.cs_ship_addr_sk=customer_address.ca_address_sk"}
    explanation = explain_tree(tree, graph)
    assert explanation.codes == frozenset()
    assert "0.750" in explanation.attachments[1].reason.text
    assert route_codes(tree, "catalog_sales", "customer_address") == ()


def test_a_declared_preference_decides_an_attachment_to_one_table(small_snapshot) -> None:
    ship = ("catalog_sales.cs_ship_addr_sk", "customer_address.ca_address_sk")
    preference = Preference(("catalog_sales", "customer_address"), (ship,), "goods are counted where they arrive")
    graph = build_graph(replace(apply_overlay(small_snapshot, parse_overlay(NAMING)), preferences=(preference,)))

    tree = _tree(graph, "catalog_sales", "customer_address")
    assert tree.attachments[1].rule == "preference"
    assert _joined(tree) == {"catalog_sales.cs_ship_addr_sk=customer_address.ca_address_sk"}
    explanation = explain_tree(tree, graph)
    assert explanation.codes == frozenset()
    assert "goods are counted where they arrive" in explanation.attachments[1].reason.text


# --------------------------------------------------------------------------
# Pairs do not combine by themselves (CHECKPOINTS.md, carried forward 32)
# --------------------------------------------------------------------------


def test_the_bridge_is_the_anchor_already_in_the_question_not_the_one_that_sorts_first(with_web_sales) -> None:
    graph = build_graph(apply_overlay(with_web_sales, parse_overlay(NAMING)))

    # Asked about the two dimensions alone, the alphabet bridges them
    # through store_sales.
    assert find_paths(graph, "store", "customer_address").selected.tables == ("store", "store_sales", "customer_address")

    # With web_sales an anchor, both attach to it, whichever is the seed.
    for anchors in (
        ("web_sales", "store", "customer_address"),
        ("store", "customer_address", "web_sales"),
        ("customer_address", "web_sales", "store"),
    ):
        tree = _tree(graph, *anchors)
        assert set(tree.tables) == {"web_sales", "store", "customer_address"}
        assert _joined(tree) == {
            "web_sales.ws_addr_sk=customer_address.ca_address_sk",
            "web_sales.ws_store_sk=store.s_store_sk",
        }
        assert explain_tree(tree, graph).codes == frozenset()


def test_item_and_date_dim_join_through_the_sales_table_in_the_question(tpcds) -> None:
    """The worked example itself. Pairwise, item to date_dim has 11 tied
    two-join routes and the alphabet picks catalog_returns. In a tree with
    store_sales, each is one join from it and nothing else is dragged in."""
    pairwise = find_paths(tpcds, "item", "date_dim")
    assert (len(pairwise.tied), pairwise.selected.tables[1]) == (11, "catalog_returns")

    for anchors in (("store_sales", "item", "date_dim"), ("item", "date_dim", "store_sales")):
        tree = _tree(tpcds, *anchors)
        assert set(tree.tables) == {"store_sales", "item", "date_dim"}
        assert _joined(tree) == {
            "store_sales.ss_item_sk=item.i_item_sk",
            "store_sales.ss_sold_date_sk=date_dim.d_date_sk",
        }
        assert explain_tree(tree, tpcds).codes == frozenset()


def test_a_table_on_a_chosen_route_joins_the_tree_and_later_anchors_may_attach_to_it(graph) -> None:
    """store and customer are two joins apart, through store_sales, which
    nobody asked for. It is in the tree all the same."""
    tree = _tree(graph, "store", "customer")
    assert tree.tables == ("store", "customer", "store_sales")
    assert tree.anchors == ("store", "customer")
    assert len(tree.joins) == 2


# --------------------------------------------------------------------------
# No basis for where to attach: multi_anchor
# --------------------------------------------------------------------------


def test_an_anchor_equally_near_two_tree_tables_is_placed_by_the_alphabet_and_says_so(graph) -> None:
    """customer_address is one join from customer (the address on file) and
    one from store_sales (the address on the order). Two places."""
    tree = _tree(graph, "store", "customer", "customer_address")
    attachment = tree.attachments[-1]

    assert (attachment.anchor, attachment.attached_to, attachment.rule) == ("customer_address", "customer", "alphabetical")
    assert len(attachment.tied) == 2
    assert [path.tables[-1] for path in attachment.tied_at_another_table] == ["store_sales"]
    assert attachment.tied_at_the_same_table == ()

    explanation = explain_tree(tree, graph)
    assert explanation.codes == {"multi_anchor"}
    (warning,) = explanation.warnings
    assert "no basis for where to join customer address" in warning.text
    assert "store sales" in warning.text and "Equally valid" in warning.text
    assert explanation.attachments[-1].alternatives[0].tables == ("customer_address", "store_sales")


def test_the_questions_wording_can_decide_where_to_attach_and_then_nothing_warns(graph) -> None:
    evidence = {
        "customer.c_current_addr_sk": 0.25,
        "store_sales.ss_addr_sk": 0.75,
        # Not telling for this tie, but on other routes the search scores.
        "customer.c_customer_sk": 0.0,
        "store_sales.ss_customer_sk": 0.0,
        "store_sales.ss_store_sk": 0.0,
        "store.s_store_sk": 0.0,
        "customer_address.ca_address_sk": 0.0,
    }
    tree = _tree(graph, "store", "customer", "customer_address", evidence=evidence, margin=0.25)
    attachment = tree.attachments[-1]

    assert (attachment.attached_to, attachment.rule) == ("store_sales", "question_evidence")
    assert [score for _, score in attachment.evidence] == [0.75, 0.25]
    explanation = explain_tree(tree, graph)
    assert explanation.codes == frozenset()
    assert "0.750" in explanation.attachments[-1].reason.text and "Not taken" in explanation.attachments[-1].reason.text


def test_evidence_within_the_margin_leaves_the_attachment_arbitrary(graph) -> None:
    evidence = dict.fromkeys(
        (
            "customer.c_current_addr_sk", "store_sales.ss_addr_sk", "customer.c_customer_sk",
            "store_sales.ss_customer_sk", "store_sales.ss_store_sk", "store.s_store_sk",
            "customer_address.ca_address_sk",
        ),
        0.0,
    )  # fmt: skip
    evidence["store_sales.ss_addr_sk"] = 0.25
    tree = _tree(graph, "store", "customer", "customer_address", evidence=evidence, margin=0.25)
    assert tree.attachments[-1].rule == "alphabetical"
    assert explain_tree(tree, graph).codes == {"multi_anchor"}


def test_recorded_the_accepted_risk_a_stores_closing_date_is_as_near_as_the_date_of_a_sale(tpcds) -> None:
    """Recorded, not wanted (ruling b, accepted risk). store_sales, store
    and date_dim: date_dim is one join from store_sales by ss_sold_date_sk
    and one join from store by s_closed_date_sk. With nothing from the
    question to separate them the alphabet chooses, and it chooses the
    closing date. Whether a question's wording rescues this is for the
    evaluation to show; this test says only what the structure does."""
    tree = _tree(tpcds, "store_sales", "store", "date_dim")
    attachment = tree.attachments[-1]

    assert (attachment.anchor, attachment.rule) == ("date_dim", "alphabetical")
    assert {path.id for path in attachment.tied} == {
        "store.s_closed_date_sk=date_dim.d_date_sk",
        "store_sales.ss_sold_date_sk=date_dim.d_date_sk",
    }
    assert attachment.attached_to == "store"
    assert explain_tree(tree, tpcds).codes == {"multi_anchor"}


def test_recorded_a_customer_is_as_near_to_a_web_page_as_to_a_web_sale(tpcds) -> None:
    """Recorded, not wanted. web_sales, web_page, customer: customer is one
    join from web_sales by the billing key and by the shipping key, and one
    join from web_page by wp_customer_sk, the weakest-evidenced edge in the
    graph (carried forward 40). Three candidates, two places. The alphabet
    reads web_page before web_sales, so with nothing from the question the
    tree takes wp_customer_sk -- no longer silently: it raises multi_anchor
    and names the two routes through the sale."""
    tree = _tree(tpcds, "web_sales", "web_page", "customer")
    attachment = tree.attachments[-1]

    assert len(attachment.tied) == 3
    assert attachment.path.id == "web_page.wp_customer_sk=customer.c_customer_sk"
    assert attachment.tied_at_the_same_table == ()
    assert sorted(path.joins[0].fk_columns[0] for path in attachment.tied_at_another_table) == [
        "ws_bill_customer_sk",
        "ws_ship_customer_sk",
    ]
    explanation = explain_tree(tree, tpcds)
    assert explanation.codes == {"multi_anchor"}
    warning = next(warning for warning in explanation.warnings if warning.code == "multi_anchor")
    assert "bill customer surrogate key" in warning.text and "ship customer surrogate key" in warning.text


def test_a_tie_at_the_same_table_and_at_another_raises_both_warnings(small_snapshot) -> None:
    """catalog_sales is one join from customer_address by two keys, and one
    join from store by a third. The alphabet takes the billing key: tied
    with the shipping key at the same table, and with the store at another."""
    snapshot = replace(
        small_snapshot,
        columns=small_snapshot.columns + (Column("catalog_sales", "cs_store_sk", "bigint"),),
        foreign_keys=small_snapshot.foreign_keys
        + (ForeignKey("catalog_sales", ("cs_store_sk",), "store", ("s_store_sk",), "catalog", "s_fk"),),
    )
    graph = build_graph(apply_overlay(snapshot, parse_overlay(NAMING)))
    tree = _tree(graph, "store_sales", "customer_address", "store", "catalog_sales")
    attachment = tree.attachments[-1]

    assert (attachment.anchor, attachment.attached_to) == ("catalog_sales", "customer_address")
    assert len(attachment.tied) == 3
    assert len(attachment.tied_at_the_same_table) == 1 and len(attachment.tied_at_another_table) == 1
    codes = explain_tree(tree, graph).codes
    assert {"arbitrary_choice", "multi_anchor"} <= codes


# --------------------------------------------------------------------------
# Rows that multiply
# --------------------------------------------------------------------------


def test_two_tables_that_both_reference_a_third_pivot_on_it(graph) -> None:
    tree = _tree(graph, "catalog_sales", "store_sales")
    assert tree.pivots == (("customer_address", ("catalog_sales", "store_sales")),)

    explanation = explain_tree(tree, graph)
    assert explanation.codes == {"arbitrary_choice", "many_to_many"}
    many = next(warning for warning in explanation.warnings if warning.code == "many_to_many")
    assert "through customer address" in many.text and "rows multiply" in many.text


def test_a_table_that_holds_the_foreign_keys_is_not_a_pivot(graph) -> None:
    tree = _tree(graph, "store_sales", "store", "customer")
    assert tree.pivots == ()
    assert explain_tree(tree, graph).codes == frozenset()


def test_a_return_and_its_sale_do_not_pivot_on_the_sale(tpcds) -> None:
    """store_sales is referenced by its returns and references everything
    else. One join reaches it by its key; that is not two."""
    tree = _tree(tpcds, "store_sales", "store_returns", "promotion")
    assert tree.pivots == ()
    assert "many_to_many" not in explain_tree(tree, tpcds).codes


# --------------------------------------------------------------------------
# Disconnection is reported, not repaired (DD-10, FR-42)
# --------------------------------------------------------------------------


def test_an_anchor_out_of_reach_declines_the_tree_and_is_named(graph) -> None:
    """catalog_sales to store takes three joins. At a limit of two there is
    no route, and none is invented."""
    assert find_paths(graph, "catalog_sales", "store").selected.length == 3

    tree = _tree(graph, "store", "catalog_sales", max_joins=2)
    assert tree.declined is True
    assert tree.decline_reason == ANCHORS_NOT_CONNECTED
    assert tree.unconnected == ("catalog_sales",)
    assert (tree.tables, tree.joins, tree.subgraph) == ((), (), ())

    explanation = explain_tree(tree, graph)
    assert explanation.declined is True
    assert explanation.codes == {"no_path"}
    assert "catalog sales" in explanation.reason and "cannot be answered" in explanation.reason


def test_a_table_with_no_keys_at_all_cannot_be_connected(small_snapshot) -> None:
    snapshot = replace(small_snapshot, tables=small_snapshot.tables + (Table("weather"),))
    graph = build_graph(apply_overlay(snapshot, parse_overlay(NAMING)))
    tree = _tree(graph, "store", "weather")
    assert (tree.declined, tree.unconnected) == (True, ("weather",))


def test_no_anchors_is_declined_as_such(graph) -> None:
    tree = build_tree(graph, (), {})
    assert (tree.declined, tree.decline_reason) == (True, NO_ANCHORS)
    assert explain_tree(tree, graph).warnings == ()


def test_one_anchor_is_a_tree_of_one_table(graph) -> None:
    tree = _tree(graph, "store")
    assert (tree.declined, tree.tables, tree.joins) == (False, ("store",), ())
    assert explain_tree(tree, graph).codes == frozenset()


# --------------------------------------------------------------------------
# The subgraph bound and the protected set (DD-11)
# --------------------------------------------------------------------------


def test_tables_on_tied_alternatives_are_added_while_the_bound_allows(with_web_sales) -> None:
    graph = build_graph(apply_overlay(with_web_sales, parse_overlay(NAMING)))
    tree = _tree(graph, "store", "customer_address", subgraph_bound=4)

    assert tree.tables == ("store", "customer_address", "store_sales")
    assert tree.subgraph == ("store", "customer_address", "store_sales", "web_sales")
    assert tree.subgraph_bound.excluded == ()


def test_alternatives_are_the_first_cut_and_the_cut_is_recorded(with_web_sales) -> None:
    graph = build_graph(apply_overlay(with_web_sales, parse_overlay(NAMING)))
    tree = _tree(graph, "store", "customer_address", subgraph_bound=3)

    assert tree.subgraph == tree.tables == ("store", "customer_address", "store_sales")
    assert tree.subgraph_bound.excluded == ("web_sales",)
    assert tree.subgraph_bound.dropped_anchors == ()


def test_when_the_tree_alone_exceeds_the_bound_the_lowest_anchor_goes_never_a_connector(graph) -> None:
    """store, customer and customer_address need store_sales between them:
    four tables. At a bound of three the lowest-scoring anchor is dropped
    and the tree rebuilt; store_sales, which nobody asked for and without
    which nothing joins, stays."""
    full = _tree(graph, "store", "customer", "customer_address")
    assert len(full.tables) == 4

    tree = _tree(graph, "store", "customer", "customer_address", subgraph_bound=3)
    assert tree.subgraph_bound.dropped_anchors == ("customer_address",)
    assert tree.tables == ("store", "customer", "store_sales")
    assert tree.declined is False
    assert tree.anchors == ("store", "customer", "customer_address")


def test_anchors_keep_being_dropped_until_the_tree_fits(graph) -> None:
    tree = _tree(graph, "store", "customer", "customer_address", subgraph_bound=2)
    assert tree.subgraph_bound.dropped_anchors == ("customer_address", "customer")
    assert tree.tables == ("store",)


def test_a_bound_that_cut_nothing_says_so(graph) -> None:
    bound = _tree(graph, "store", "store_sales").subgraph_bound
    assert (bound.limit, bound.dropped_anchors, bound.excluded) == (10, (), ())


# --------------------------------------------------------------------------
# A rival that is not in the tree (ruling c)
# --------------------------------------------------------------------------


def test_a_rival_set_aside_and_absent_from_the_tree_is_an_ambiguity(graph) -> None:
    aside = (SetAside("address", "customer_address", "store", 0.9, 0.85),)
    tree = _tree(graph, "store_sales", "customer_address", set_aside=aside)
    assert tree.ambiguities == aside

    explanation = explain_tree(tree, graph)
    assert explanation.codes == {"anchor_ambiguity"}
    (warning,) = explanation.warnings
    assert '"address"' in warning.text and "could as well mean store" in warning.text
    assert "0.900" in warning.text and "0.850" in warning.text


def test_a_rival_that_is_in_the_tree_anyway_changes_nothing_and_does_not_warn(graph) -> None:
    """Choosing it would not give a different tree: it is already there."""
    aside = (SetAside("address", "customer_address", "store_sales", 0.9, 0.85),)
    tree = _tree(graph, "store", "customer_address", set_aside=aside)
    assert "store_sales" in tree.tables and "store_sales" not in tree.anchors
    assert tree.ambiguities == ()
    assert "anchor_ambiguity" not in explain_tree(tree, graph).codes


def test_a_rival_of_a_table_that_is_not_in_the_tree_does_not_warn(graph) -> None:
    aside = (SetAside("address", "customer_address", "catalog_sales", 0.9, 0.85),)
    assert _tree(graph, "store", "store_sales", set_aside=aside).ambiguities == ()


# --------------------------------------------------------------------------
# The path between two tables within the tree
# --------------------------------------------------------------------------


def test_the_route_between_two_tables_is_read_within_the_tree(graph) -> None:
    tree = _tree(graph, "store", "customer", "store_sales")
    route = tree.route("store", "customer")
    assert route.tables == ("store", "store_sales", "customer")
    assert [join.walked for join in route.joins] == ["one_to_many", "many_to_one"]
    assert tree.route("customer", "store").tables == ("customer", "store_sales", "store")


def test_a_table_the_tree_does_not_hold_has_no_route(graph) -> None:
    tree = _tree(graph, "store", "store_sales")
    assert tree.route("store", "customer_address") is None
    assert route_codes(tree, "store", "customer_address") == ()


def test_a_route_carries_the_warnings_about_its_own_joins_and_no_others(graph) -> None:
    """store_sales to catalog_sales through customer_address: the billing
    and shipping keys tie, and the route pivots. store_sales to
    customer_address alone has one key and does neither."""
    tree = _tree(graph, "store_sales", "customer_address", "catalog_sales")
    assert explain_tree(tree, graph).codes == {"arbitrary_choice", "many_to_many"}

    assert route_codes(tree, "store_sales", "catalog_sales") == ("arbitrary_choice", "many_to_many")
    assert route_codes(tree, "customer_address", "catalog_sales") == ("arbitrary_choice",)
    assert route_codes(tree, "store_sales", "customer_address") == ()


def test_a_route_carries_multi_anchor_when_its_join_was_placed_by_the_alphabet(graph) -> None:
    tree = _tree(graph, "store", "customer", "customer_address")
    assert route_codes(tree, "customer", "customer_address") == ("multi_anchor",)
    assert route_codes(tree, "store", "customer") == ()


# --------------------------------------------------------------------------
# What it refuses
# --------------------------------------------------------------------------


def test_bad_arguments_raise(graph) -> None:
    with pytest.raises(ValueError, match="named twice"):
        build_tree(graph, ("store", "store"), {"store": 1.0})
    with pytest.raises(ValueError, match="no score was supplied"):
        build_tree(graph, ("store", "customer"), {"store": 1.0})
    with pytest.raises(ValueError, match="subgraph bound"):
        build_tree(graph, ("store",), {"store": 1.0}, subgraph_bound=0)
