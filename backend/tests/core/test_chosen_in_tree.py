"""A route the model named for this question alone (step 10b, v2.1; the
owner's rulings at stop A2, 20 October 2026).

`build_tree(..., chosen=...)` takes the ids of routes named for one
question. One decides an attachment only where the alphabet would have:
after the wording, a declared preference and the narrowing between
places, and only when exactly one of the routes left is named. Its rule
is `model_choice`, never a rule's or the overlay's, and the Explainer
raises neither arbitrary_choice nor multi_anchor for it.

With `chosen` absent or empty, every tree is what it was: held to
tree_golden.json, written from join_tree before `chosen` existed
(make_tree_golden.py).
"""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from make_tree_golden import MARGIN, digest, evidence_of
from tpcds_files import OVERLAY, ddl_snapshot

from app.core.explainer import explain_tree, route_codes
from app.core.graph_builder import build_graph
from app.core.join_tree import build_tree
from app.core.overlay import apply_overlay, parse_overlay
from app.core.snapshot import Preference

NAMING = """
naming:
  prefixes: { cs_: catalog sales, ss_: store sales, c_: customer, ca_: customer address, s_: store, ws_: web sales }
  words:    { sk: surrogate key, addr: address }
"""
GOLDEN = json.loads((Path(__file__).resolve().parent / "tree_golden.json").read_text())


@pytest.fixture
def graph(small_snapshot):
    return build_graph(apply_overlay(small_snapshot, parse_overlay(NAMING)))


@pytest.fixture(scope="module")
def tpcds():
    return build_graph(apply_overlay(ddl_snapshot(), parse_overlay(OVERLAY.read_text())))


def _scores(*anchors: str) -> dict[str, float]:
    return {anchor: 1.0 - position / 10 for position, anchor in enumerate(anchors)}


def _tree(graph, *anchors: str, **options):
    return build_tree(graph, anchors, _scores(*anchors), **options)


def _route(attachment, column: str):
    """The tied route of an attachment that uses `column`."""
    (path,) = [path for path in attachment.tied if any(column in edge for pair in path.edges for edge in pair)]
    return path


# --------------------------------------------------------------------------
# Nothing named: every tree as it was
# --------------------------------------------------------------------------


def _golden_inputs():
    keys = [key for key in GOLDEN if "|" not in key]
    pairs = [key for key in keys if key.count(",") == 1]
    triples = [key for key in keys if key.count(",") == 2]
    return pairs + triples[::10]


@pytest.mark.parametrize("chosen", [None, frozenset()], ids=["absent", "empty"])
def test_with_nothing_named_every_tree_is_as_it_was(tpcds, chosen) -> None:
    evidence = evidence_of(tpcds)
    extra = {} if chosen is None else {"chosen": chosen}
    for key in _golden_inputs():
        anchors = tuple(key.split(","))
        assert digest(build_tree(tpcds, anchors, _scores(*anchors), **extra)) == GOLDEN[key], key
        with_evidence = build_tree(tpcds, anchors, _scores(*anchors), evidence=evidence, margin=MARGIN, **extra)
        assert digest(with_evidence) == GOLDEN[key + "|evidence"], key


# --------------------------------------------------------------------------
# A tie at one table
# --------------------------------------------------------------------------


def test_a_route_named_decides_a_tie_the_alphabet_would_have_broken(graph) -> None:
    before = _tree(graph, "catalog_sales", "customer_address")
    assert before.attachments[1].rule == "alphabetical"
    ship = _route(before.attachments[1], "catalog_sales.cs_ship_addr_sk")

    tree = _tree(graph, "catalog_sales", "customer_address", chosen=frozenset({ship.id}))
    attachment = tree.attachments[1]
    assert attachment.rule == "model_choice" and not attachment.arbitrary
    assert attachment.path.id == ship.id
    assert {path.id for path in attachment.tied} == {path.id for path in before.attachments[1].tied}
    assert tree.subgraph == before.subgraph
    explanation = explain_tree(tree, graph)
    assert explanation.codes == frozenset()
    assert route_codes(tree, "catalog_sales", "customer_address") == ()


def test_the_explainer_says_the_model_chose_and_never_that_the_alphabet_or_a_rule_did(graph) -> None:
    before = _tree(graph, "catalog_sales", "customer_address")
    ship = _route(before.attachments[1], "catalog_sales.cs_ship_addr_sk")
    tree = _tree(graph, "catalog_sales", "customer_address", chosen=frozenset({ship.id}))
    reason = explain_tree(tree, graph).attachments[1].reason
    assert reason.rule == "model_choice"
    assert "the model, reading the question, named this one" in reason.text
    assert "alphabetically" not in reason.text and "arbitrary" not in reason.text
    assert "overlay" not in reason.text and "wording of the question points" not in reason.text
    assert "Not taken: " in reason.text and "bill address surrogate key" in reason.text


def test_naming_the_alphabets_own_pick_is_still_the_models_choice(graph) -> None:
    before = _tree(graph, "catalog_sales", "customer_address")
    bill = before.attachments[1].path
    tree = _tree(graph, "catalog_sales", "customer_address", chosen=frozenset({bill.id}))
    assert tree.attachments[1].rule == "model_choice" and tree.attachments[1].path.id == bill.id
    assert tree.joins == before.joins


def test_the_questions_wording_outranks_a_route_named(graph) -> None:
    evidence = {"catalog_sales.cs_bill_addr_sk": 0.25, "catalog_sales.cs_ship_addr_sk": 0.75}
    plain = _tree(graph, "catalog_sales", "customer_address")
    bill = plain.attachments[1].path
    tree = _tree(graph, "catalog_sales", "customer_address", evidence=evidence, margin=0.25,
                 chosen=frozenset({bill.id}))  # fmt: skip
    assert tree.attachments[1].rule == "question_evidence"
    assert "cs_ship_addr_sk" in tree.attachments[1].path.id


def test_a_declared_preference_outranks_a_route_named(small_snapshot) -> None:
    prefer = Preference(
        ("catalog_sales", "customer_address"),
        (("catalog_sales.cs_ship_addr_sk", "customer_address.ca_address_sk"),),
        "shipping is what is meant",
    )
    graph = build_graph(replace(apply_overlay(small_snapshot, parse_overlay(NAMING)), preferences=(prefer,)))
    plain = _tree(graph, "catalog_sales", "customer_address")
    assert plain.attachments[1].rule == "preference"
    bill = _route(plain.attachments[1], "catalog_sales.cs_bill_addr_sk")
    tree = _tree(graph, "catalog_sales", "customer_address", chosen=frozenset({bill.id}))
    assert tree.attachments[1].rule == "preference" and "cs_ship_addr_sk" in tree.attachments[1].path.id


def test_two_routes_of_one_tie_named_decide_nothing(graph) -> None:
    before = _tree(graph, "catalog_sales", "customer_address")
    both = frozenset(path.id for path in before.attachments[1].tied)
    tree = _tree(graph, "catalog_sales", "customer_address", chosen=both)
    assert tree.attachments[1].rule == "alphabetical" and tree == before


def test_a_route_named_that_is_not_among_the_tied_decides_nothing(graph) -> None:
    before = _tree(graph, "catalog_sales", "customer_address")
    tree = _tree(graph, "catalog_sales", "customer_address", chosen=frozenset({"store.s_store_sk=x.y"}))
    assert tree == before


# --------------------------------------------------------------------------
# A tie between places
# --------------------------------------------------------------------------


def test_a_route_named_decides_where_to_attach(graph) -> None:
    before = _tree(graph, "store", "customer", "customer_address")
    last = before.attachments[-1]
    assert (last.attached_to, last.rule) == ("customer", "alphabetical")
    (at_the_sale,) = last.tied_at_another_table

    tree = _tree(graph, "store", "customer", "customer_address", chosen=frozenset({at_the_sale.id}))
    attachment = tree.attachments[-1]
    assert (attachment.attached_to, attachment.rule) == ("store_sales", "model_choice")
    assert attachment.path.id == at_the_sale.id
    assert {path.id for path in attachment.tied} == {path.id for path in last.tied}
    assert explain_tree(tree, graph).codes == frozenset()


def test_a_route_a_declared_preference_withdrew_cannot_be_named_back(tpcds) -> None:
    """H4's shape. web_sales and customer are in the tree; date_dim is one
    join from each. The declared route preference withdraws the ship date
    at web_sales, but cannot choose between places: the alphabet takes
    customer. The sold date can be named; the ship date, withdrawn, cannot."""
    before = _tree(tpcds, "web_sales", "customer", "date_dim")
    last = before.attachments[-1]
    assert (last.anchor, last.attached_to, last.rule) == ("date_dim", "customer", "alphabetical")
    (ship,) = [path for path in last.withdrawn if "ws_ship_date_sk" in path.id]
    (sold,) = [path for path in last.tied if "ws_sold_date_sk" in path.id]

    assert _tree(tpcds, "web_sales", "customer", "date_dim", chosen=frozenset({ship.id})) == before
    tree = _tree(tpcds, "web_sales", "customer", "date_dim", chosen=frozenset({sold.id}))
    attachment = tree.attachments[-1]
    assert (attachment.attached_to, attachment.rule, attachment.path.id) == ("web_sales", "model_choice", sold.id)
    assert attachment.withdrawn == last.withdrawn and attachment.route_preferences == last.route_preferences
    assert route_codes(tree, "web_sales", "date_dim") == ()
    # customer's own tie, the bill or the ship key to web_sales, was named
    # by nothing: it stays the alphabet's, and says so.
    assert tree.attachments[1].rule == "alphabetical" and "arbitrary_choice" in explain_tree(tree, tpcds).codes


def test_the_wording_deciding_where_to_attach_outranks_a_route_named(graph) -> None:
    evidence = {
        "customer.c_current_addr_sk": 0.25, "store_sales.ss_addr_sk": 0.75, "customer.c_customer_sk": 0.0,
        "store_sales.ss_customer_sk": 0.0, "store_sales.ss_store_sk": 0.0, "store.s_store_sk": 0.0,
        "customer_address.ca_address_sk": 0.0,
    }  # fmt: skip
    plain = _tree(graph, "store", "customer", "customer_address")
    at_customer = plain.attachments[-1].path
    tree = _tree(graph, "store", "customer", "customer_address", evidence=evidence, margin=0.25,
                 chosen=frozenset({at_customer.id}))  # fmt: skip
    assert (tree.attachments[-1].attached_to, tree.attachments[-1].rule) == ("store_sales", "question_evidence")


def test_two_places_named_decide_nothing(graph) -> None:
    before = _tree(graph, "store", "customer", "customer_address")
    both = frozenset(path.id for path in before.attachments[-1].tied)
    assert _tree(graph, "store", "customer", "customer_address", chosen=both) == before
