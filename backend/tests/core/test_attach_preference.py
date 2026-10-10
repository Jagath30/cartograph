"""A declared preference between two PLACES to attach (DD-12, DD-16; ruling
C of the retrieval pass between steps 7 and 8, 10 October 2026).

    preferences:
      - attach_to:   catalog_sales
        rather_than: catalog_returns
        because: ...

On the committed TPC-DS DDL and overlay, with the preference added here by
hand, so each test says what it declares. No database, no score from an
embedding, and no test reads the evaluation set.
"""

from dataclasses import replace

import pytest
from tpcds_files import OVERLAY, ddl_snapshot

from app.core.explainer import explain_tree
from app.core.graph_builder import build_graph, column_nodes
from app.core.join_tree import build_tree
from app.core.overlay import apply_overlay, parse_overlay
from app.core.snapshot import AttachPreference

CATALOG = AttachPreference("catalog_sales", "catalog_returns", "a return refers to its sale")
STORE = AttachPreference("store_sales", "store_returns", "a return refers to its sale")


@pytest.fixture(scope="module")
def snapshot():
    return apply_overlay(ddl_snapshot(), parse_overlay(OVERLAY.read_text()))


def _graph(snapshot, *preferences: AttachPreference):
    return build_graph(replace(snapshot, attach_preferences=preferences))


def _tree(graph, *anchors: str, evidence: dict[str, float] | None = None, margin: float = 0.0):
    scores = {anchor: 1.0 - position / 10 for position, anchor in enumerate(anchors)}
    if evidence is not None:
        evidence = {**{column: 0.0 for column in column_nodes(graph)}, **evidence}
    return build_tree(graph, anchors, scores, evidence=evidence, margin=margin)


def _last(tree):
    return tree.attachments[-1]


def _fk(attachment) -> str:
    return ", ".join(".".join((join.fk_table, *join.fk_columns)) for join in attachment.path.joins)


# --------------------------------------------------------------------------
# What it decides
# --------------------------------------------------------------------------


def test_without_it_the_alphabet_hangs_a_dimension_on_the_returns_table(snapshot) -> None:
    """The lean this preference exists for (CHECKPOINTS item 65):
    catalog_returns sorts before catalog_sales."""
    graph = _graph(snapshot)
    tree = _tree(graph, "catalog_sales", "catalog_returns", "catalog_page")
    assert (_last(tree).attached_to, _last(tree).rule) == ("catalog_returns", "alphabetical")
    assert explain_tree(tree, graph).codes == {"multi_anchor"}


def test_one_candidate_left_is_chosen_by_the_preference_and_nothing_warns(snapshot) -> None:
    graph = _graph(snapshot, CATALOG)
    tree = _tree(graph, "catalog_sales", "catalog_returns", "catalog_page")
    last = _last(tree)

    assert (last.attached_to, last.rule) == ("catalog_sales", "preference")
    assert _fk(last) == "catalog_sales.cs_catalog_page_sk"
    assert last.attach_preferences == (CATALOG,)
    assert [_fk_of(path) for path in last.withdrawn] == ["catalog_returns.cr_catalog_page_sk"]
    assert last.arbitrary is False

    explanation = explain_tree(tree, graph)
    assert explanation.codes == frozenset()
    reason = explanation.attachments[-1].reason
    assert reason.rule == "preference"
    assert "a return refers to its sale" in reason.text
    assert "catalog sales rather than catalog returns" in reason.text
    # The route not taken is still told (DD-21: nothing is hidden).
    assert "Not taken:" in reason.text and "catalog returns" in reason.text


def _fk_of(path) -> str:
    return ", ".join(".".join((join.fk_table, *join.fk_columns)) for join in path.joins)


def test_several_left_go_to_the_alphabet_and_warn_as_before(snapshot) -> None:
    """Billing against shipping stays arbitrary: the preference says which
    table, never which key."""
    graph = _graph(snapshot, CATALOG)
    tree = _tree(graph, "catalog_sales", "catalog_returns", "customer_address")
    last = _last(tree)

    assert (last.attached_to, last.rule) == ("catalog_sales", "alphabetical")
    assert _fk(last) == "catalog_sales.cs_bill_addr_sk"
    assert sorted(_fk_of(path) for path in last.tied) == ["catalog_sales.cs_bill_addr_sk", "catalog_sales.cs_ship_addr_sk"]
    assert sorted(_fk_of(path) for path in last.withdrawn) == [
        "catalog_returns.cr_refunded_addr_sk", "catalog_returns.cr_returning_addr_sk",
    ]  # fmt: skip
    assert last.attach_preferences == (CATALOG,)

    explanation = explain_tree(tree, graph)
    assert explanation.codes == {"arbitrary_choice"}
    (warning,) = explanation.warnings
    assert "ship address" in warning.text
    assert "refunded" not in warning.text and "returning" not in warning.text
    assert "catalog sales rather than catalog returns" in explanation.attachments[-1].reason.text


def test_a_third_place_is_still_a_tie_between_places(snapshot) -> None:
    """customer is one join from the sale, the return and the address. The
    preference withdraws the return; the sale and the address remain, the
    alphabet chooses, and both warnings say so."""
    graph = _graph(snapshot, CATALOG)
    tree = _tree(graph, "catalog_sales", "catalog_returns", "customer_address", "customer")
    last = _last(tree)

    assert (last.anchor, last.attached_to, last.rule) == ("customer", "catalog_sales", "alphabetical")
    assert {path.tables[-1] for path in last.tied} == {"catalog_sales", "customer_address"}
    assert {path.tables[-1] for path in last.withdrawn} == {"catalog_returns"}
    codes = {warning.code for warning in explain_tree(tree, graph).warnings if "customer" in warning.text.split(".")[1]}
    assert codes == {"arbitrary_choice", "multi_anchor"}


# --------------------------------------------------------------------------
# The order: shortest, the question's wording, the preference, the alphabet
# --------------------------------------------------------------------------


def test_the_questions_wording_comes_first_so_a_question_about_returns_keeps_the_return(snapshot) -> None:
    graph = _graph(snapshot, CATALOG)
    evidence = {"catalog_returns.cr_catalog_page_sk": 0.75, "catalog_sales.cs_catalog_page_sk": 0.25}
    tree = _tree(graph, "catalog_sales", "catalog_returns", "catalog_page", evidence=evidence, margin=0.25)
    last = _last(tree)

    assert (last.attached_to, last.rule) == ("catalog_returns", "question_evidence")
    assert last.withdrawn == () and last.attach_preferences == ()


def test_wording_within_the_margin_leaves_it_to_the_preference(snapshot) -> None:
    """Exactly the margin is not beyond it: 0.75 - 0.5 is exactly 0.25."""
    graph = _graph(snapshot, CATALOG)
    evidence = {"catalog_returns.cr_catalog_page_sk": 0.75, "catalog_sales.cs_catalog_page_sk": 0.5}
    tree = _tree(graph, "catalog_sales", "catalog_returns", "catalog_page", evidence=evidence, margin=0.25)
    assert (_last(tree).attached_to, _last(tree).rule) == ("catalog_sales", "preference")


def test_the_wording_is_consulted_once_and_not_again_after_the_preference(snapshot) -> None:
    """Over all four candidates the two returns keys lead and are level, so
    the wording decides nothing. Among the two the preference leaves, the
    shipping key is far ahead. It is not asked again: the alphabet takes
    the billing key and says the choice was arbitrary."""
    graph = _graph(snapshot, CATALOG)
    evidence = {
        "catalog_returns.cr_refunded_addr_sk": 1.0,
        "catalog_returns.cr_returning_addr_sk": 1.0,
        "catalog_sales.cs_ship_addr_sk": 0.75,
        "catalog_sales.cs_bill_addr_sk": 0.0,
    }
    tree = _tree(graph, "catalog_sales", "catalog_returns", "customer_address", evidence=evidence, margin=0.25)
    last = _last(tree)

    assert (last.rule, _fk(last)) == ("alphabetical", "catalog_sales.cs_bill_addr_sk")
    assert explain_tree(tree, graph).codes == {"arbitrary_choice"}


# --------------------------------------------------------------------------
# What it does not touch
# --------------------------------------------------------------------------


def test_it_needs_both_tables_among_the_places(snapshot) -> None:
    """No catalog_sales in the tree: customer still ties between the return
    and the address, and the preference has nothing to say."""
    plain = _tree(_graph(snapshot), "catalog_returns", "customer_address", "customer")
    declared = _tree(_graph(snapshot, CATALOG), "catalog_returns", "customer_address", "customer")
    assert declared == plain
    assert _last(declared).rule == "alphabetical" and _last(declared).withdrawn == ()


def test_it_is_not_about_the_table_being_attached(snapshot) -> None:
    """catalog_returns itself is one join from its sale and from the
    address. Where it attaches is not this preference's business."""
    plain = _tree(_graph(snapshot), "catalog_sales", "customer_address", "catalog_returns")
    declared = _tree(_graph(snapshot, CATALOG), "catalog_sales", "customer_address", "catalog_returns")
    assert _last(plain).anchor == "catalog_returns" and len({p.tables[-1] for p in _last(plain).tied}) == 2
    assert declared == plain


def test_another_channels_preference_changes_nothing(snapshot) -> None:
    plain = _tree(_graph(snapshot), "catalog_sales", "catalog_returns", "catalog_page")
    declared = _tree(_graph(snapshot, STORE), "catalog_sales", "catalog_returns", "catalog_page")
    assert declared == plain


def test_two_keys_to_one_table_are_untouched(snapshot) -> None:
    """Billing against shipping with nothing else in the tree: one place,
    so this is the PathFinder's tie, arbitrary and warned as at step 4."""
    graph = _graph(snapshot, CATALOG, STORE)
    tree = _tree(graph, "catalog_sales", "customer_address")
    assert _last(tree).rule == "alphabetical"
    assert explain_tree(tree, graph).codes == {"arbitrary_choice"}
    assert tree == _tree(_graph(snapshot), "catalog_sales", "customer_address")


def test_the_demonstration_tie_between_a_store_and_an_address_is_untouched(snapshot) -> None:
    plain = _tree(_graph(snapshot), "store", "customer_address", "store_sales")
    declared = _tree(_graph(snapshot, CATALOG, STORE), "store", "customer_address", "store_sales")
    assert declared == plain


def test_a_bridge_inside_a_route_is_not_a_place(snapshot) -> None:
    """item reaches customer in two joins through any sale or return. Every
    candidate ends at customer: one place, and nothing is withdrawn."""
    plain = _tree(_graph(snapshot), "customer", "item")
    declared = _tree(_graph(snapshot, CATALOG, STORE), "customer", "item")
    assert declared == plain
    assert "catalog_returns" in plain.tables  # the alphabet's bridge, still


def test_it_applies_at_two_joins_as_at_one(snapshot) -> None:
    """call_center is two joins from the store's sale and from its return,
    through the date. The routes that end at the return are withdrawn."""
    graph = _graph(snapshot, STORE)
    tree = _tree(graph, "store_sales", "store_returns", "call_center")
    last = _last(tree)
    assert last.path.length == 2 and last.attached_to == "store_sales"
    assert {path.tables[-1] for path in last.withdrawn} == {"store_returns"}
    assert {path.tables[-1] for path in last.tied} == {"store_sales"}


def test_tables_on_withdrawn_routes_are_still_offered_beside_the_tree(snapshot) -> None:
    """The subgraph is what the model is shown. Withdrawing a route must
    not shrink it. web_returns reaches the store's return through `reason`,
    which the store's sale has no key to: that table lies only on routes
    the preference withdraws, and is still offered."""
    anchors = ("store_sales", "store_returns", "web_returns")
    scores = {"store_sales": 1.0, "store_returns": 0.9, "web_returns": 0.8}
    plain = build_tree(_graph(snapshot), anchors, scores, subgraph_bound=24)
    declared = build_tree(_graph(snapshot, STORE), anchors, scores, subgraph_bound=24)

    assert "reason" in {table for path in _last(declared).withdrawn for table in path.tables}
    assert "reason" not in {table for path in _last(declared).tied for table in path.tables}
    assert "reason" in declared.subgraph
    assert set(declared.subgraph) == set(plain.subgraph)


# --------------------------------------------------------------------------
# The overlay: parsed strictly, checked against the warehouse
# --------------------------------------------------------------------------

ENTRY = "preferences:\n  - { attach_to: catalog_sales, rather_than: catalog_returns, because: a  return\n      refers to its sale }\n"


def test_an_attach_preference_is_parsed_and_its_reason_tidied() -> None:
    overlay = parse_overlay(ENTRY)
    assert overlay.attach_preferences == (CATALOG,)
    assert overlay.preferences == ()


@pytest.mark.parametrize(
    "entry",
    [
        "{ attach_to: a, rather_than: b }",  # no reason
        "{ attach_to: a, rather_than: b, because: '' }",
        "{ attach_to: a, because: r }",
        "{ attach_to: a, rather_than: a, because: r }",  # the same table twice
        "{ attach_to: a.x, rather_than: b, because: r }",  # a column, not a table
        "{ attach_to: [a], rather_than: b, because: r }",
        "{ attach_to: a, rather_than: b, because: r, between: [a, b] }",  # the two kinds mixed
        "{ attach_to: a, rather_than: b, because: r, weight: 2 }",
    ],
)
def test_a_malformed_attach_preference_is_refused(entry: str) -> None:
    with pytest.raises(ValueError, match="overlay"):
        parse_overlay(f"preferences:\n  - {entry}\n")


def test_the_same_pair_twice_or_both_ways_round_is_refused() -> None:
    one = "  - { attach_to: a, rather_than: b, because: r }\n"
    with pytest.raises(ValueError, match="more than one"):
        parse_overlay("preferences:\n" + one + one)
    with pytest.raises(ValueError, match="more than one"):
        parse_overlay("preferences:\n" + one + "  - { attach_to: b, rather_than: a, because: r }\n")


def test_both_tables_must_exist_and_be_joined_by_a_foreign_key(small_snapshot) -> None:
    def declare(attach_to: str, rather_than: str):
        text = f"preferences:\n  - {{ attach_to: {attach_to}, rather_than: {rather_than}, because: r }}\n"
        return apply_overlay(small_snapshot, parse_overlay(text))

    # A sale refers to its store: joined, either way round.
    assert len(declare("store", "store_sales").attach_preferences) == 1
    assert len(declare("store_sales", "store").attach_preferences) == 1
    with pytest.raises(ValueError, match="no table"):
        declare("store", "nowhere")
    with pytest.raises(ValueError, match="no table"):
        declare("nowhere", "store")
    # Two tables with no key between them are not a pair this can rank.
    with pytest.raises(ValueError, match="no foreign key joins"):
        declare("store", "customer_address")


def test_the_graph_carries_them(small_snapshot) -> None:
    text = "preferences:\n  - { attach_to: store, rather_than: store_sales, because: r }\n"
    after = apply_overlay(small_snapshot, parse_overlay(text))
    assert build_graph(after).graph["attach_preferences"] == after.attach_preferences
    assert build_graph(small_snapshot).graph["attach_preferences"] == ()
