"""A route preference where an attachment has several places (DD-12; ruling
D of the retrieval pass between steps 7 and 8, 10 October 2026).

    preferences:
      - between: [catalog_sales, date_dim]
        prefer:  [{ from: catalog_sales.cs_sold_date_sk, to: date_dim.d_date_sk }]
        because: a question that names no date means the date of sale

Between two tables this is the PathFinder's, and was already built. In a
tree, date_dim is often one join from several tables at once, and there a
route preference used to be reported as not applied. Now it withdraws the
other routes to ITS table and says nothing of the other places.

On the committed TPC-DS DDL and overlay, with the preferences set here.
"""

from dataclasses import replace

import pytest
from tpcds_files import OVERLAY, ddl_snapshot

from app.core.explainer import explain, explain_tree
from app.core.graph_builder import build_graph, column_nodes
from app.core.join_tree import build_tree
from app.core.overlay import apply_overlay, parse_overlay
from app.core.path_finder import find_paths
from app.core.snapshot import AttachPreference, Preference

SOLD = Preference(
    ("catalog_sales", "date_dim"),
    (("catalog_sales.cs_sold_date_sk", "date_dim.d_date_sk"),),
    "a question that names no date means the date of sale",
)
CATALOG = AttachPreference("catalog_sales", "catalog_returns", "a return refers to its sale")
SHIP, SOLD_KEY = "catalog_sales.cs_ship_date_sk", "catalog_sales.cs_sold_date_sk"


@pytest.fixture(scope="module")
def snapshot():
    return apply_overlay(ddl_snapshot(), parse_overlay(OVERLAY.read_text()))


def _graph(snapshot, routes=(), places=()):
    return build_graph(replace(snapshot, preferences=tuple(routes), attach_preferences=tuple(places)))


def _all(graph, **scores: float) -> dict[str, float]:
    return {**{column: 0.0 for column in column_nodes(graph)}, **scores}


def _tree(graph, *anchors: str, evidence=None, margin: float = 0.0):
    scores = {anchor: 1.0 - position / 10 for position, anchor in enumerate(anchors)}
    return build_tree(graph, anchors, scores, evidence=evidence, margin=margin)


def _fk(path) -> str:
    return ", ".join(".".join((join.fk_table, *join.fk_columns)) for join in path.joins)


def _date(tree):
    return next(attachment for attachment in tree.attachments if attachment.anchor == "date_dim")


def _about_the_date(explanation) -> set[str]:
    return {warning.code for warning in explanation.warnings if "date dimension" in warning.text.split(" Used:")[0]}


# --------------------------------------------------------------------------
# Several places: the date is one join from the sale and from the promotion
# --------------------------------------------------------------------------


def test_without_it_the_alphabet_takes_the_ship_date(snapshot) -> None:
    graph = _graph(snapshot)
    tree = _tree(graph, "catalog_sales", "promotion", "date_dim")
    assert (_date(tree).rule, _fk(_date(tree).path)) == ("alphabetical", SHIP)
    assert _about_the_date(explain_tree(tree, graph)) == {"arbitrary_choice", "multi_anchor"}


def test_it_withdraws_the_other_key_to_its_table_and_leaves_the_other_place(snapshot) -> None:
    graph = _graph(snapshot, routes=(SOLD,))
    tree = _tree(graph, "catalog_sales", "promotion", "date_dim")
    date = _date(tree)

    assert _fk(date.path) == SOLD_KEY
    assert [_fk(path) for path in date.withdrawn] == [SHIP]
    assert sorted(_fk(path) for path in date.tied) == [
        SOLD_KEY, "promotion.p_end_date_sk", "promotion.p_start_date_sk",
    ]  # fmt: skip
    assert date.route_preferences == (SOLD,)
    assert date.preference_not_applied is None
    # Still a tie between two places, still the alphabet's, still said.
    assert date.rule == "alphabetical"
    explanation = explain_tree(tree, graph)
    assert _about_the_date(explanation) == {"multi_anchor"}
    assert "preference_not_applied" not in explanation.codes
    reason = next(a.reason.text for a in explanation.attachments if a.anchor == "date_dim")
    assert "a question that names no date means the date of sale" in reason
    assert "Not taken:" in reason and "ship date" in reason


def test_with_the_place_preference_one_route_is_left_and_nothing_warns(snapshot) -> None:
    """The sale, its return and the date: C withdraws the return, D the ship
    date. One route is left, chosen by declared preferences, quietly."""
    graph = _graph(snapshot, routes=(SOLD,), places=(CATALOG,))
    tree = _tree(graph, "catalog_sales", "catalog_returns", "date_dim")
    date = _date(tree)

    assert (date.rule, _fk(date.path)) == ("preference", SOLD_KEY)
    assert sorted(_fk(path) for path in date.withdrawn) == ["catalog_returns.cr_returned_date_sk", SHIP]
    assert (date.attach_preferences, date.route_preferences) == ((CATALOG,), (SOLD,))
    explanation = explain_tree(tree, graph)
    assert _about_the_date(explanation) == set()
    reason = next(a.reason.text for a in explanation.attachments if a.anchor == "date_dim")
    assert "a return refers to its sale" in reason and "the date of sale" in reason


def test_the_wording_comes_first_and_a_question_about_shipping_keeps_the_ship_date(snapshot) -> None:
    graph = _graph(snapshot, routes=(SOLD,))
    evidence = _all(graph, **{SHIP: 0.75, SOLD_KEY: 0.25})
    tree = _tree(graph, "catalog_sales", "promotion", "date_dim", evidence=evidence, margin=0.25)
    date = _date(tree)

    assert (date.rule, _fk(date.path)) == ("question_evidence", SHIP)
    assert date.withdrawn == () and date.route_preferences == ()
    # Outranked by the wording, which is not a fault of the preference.
    assert date.preference_outranked == SOLD and date.preference_not_applied is None
    explanation = explain_tree(tree, graph)
    assert "preference_not_applied" not in explanation.codes
    reason = next(a.reason.text for a in explanation.attachments if a.anchor == "date_dim")
    assert "the wording of the question outranks it" in reason


def test_wording_within_the_margin_leaves_it_to_the_preference(snapshot) -> None:
    graph = _graph(snapshot, routes=(SOLD,))
    evidence = _all(graph, **{SHIP: 0.75, SOLD_KEY: 0.5})
    tree = _tree(graph, "catalog_sales", "promotion", "date_dim", evidence=evidence, margin=0.25)
    assert _fk(_date(tree).path) == SOLD_KEY


def test_it_cannot_move_the_table_to_its_own_place(snapshot) -> None:
    """catalog_page sorts before catalog_sales and has two date keys of its
    own. The preference withdraws the ship date and no more; the alphabet
    still takes the page's end date. It is a preference between two keys of
    one table, not for that table over another."""
    graph = _graph(snapshot, routes=(SOLD,))
    tree = _tree(graph, "catalog_sales", "catalog_page", "date_dim")
    date = _date(tree)
    assert (date.attached_to, date.rule) == ("catalog_page", "alphabetical")
    assert [_fk(path) for path in date.withdrawn] == [SHIP]


def test_a_preference_that_names_none_of_the_candidates_is_still_reported(snapshot) -> None:
    """Declared for the sale and the date, but the sale is not in this tree:
    as before, it is reported as not applied only where its pair was asked.
    Here its pair is never asked, and nothing is said."""
    graph = _graph(snapshot, routes=(SOLD,))
    tree = _tree(graph, "promotion", "store", "date_dim")
    date = _date(tree)
    assert date.route_preferences == () and date.withdrawn == ()
    assert tree == _tree(_graph(snapshot), "promotion", "store", "date_dim")


def test_a_route_through_the_date_is_not_the_route_it_names(snapshot) -> None:
    """call_center reaches catalog_sales directly; item reaches promotion
    directly. A two-join route that passes through date_dim is not a route
    between catalog_sales and date_dim, and is left alone: web_page to
    catalog_sales through the date ties as it did."""
    plain = _tree(_graph(snapshot), "catalog_sales", "web_page")
    declared = _tree(_graph(snapshot, routes=(SOLD,)), "catalog_sales", "web_page")
    assert declared == plain


def test_other_date_keys_are_untouched(snapshot) -> None:
    for anchors in (("customer", "date_dim"), ("web_sales", "date_dim"), ("promotion", "date_dim")):
        assert _tree(_graph(snapshot, routes=(SOLD,)), *anchors) == _tree(_graph(snapshot), *anchors)


# --------------------------------------------------------------------------
# One place: the PathFinder's own, with the order of C
# --------------------------------------------------------------------------


def test_between_the_two_tables_alone_the_preference_decides_quietly(snapshot) -> None:
    graph = _graph(snapshot, routes=(SOLD,))
    result = find_paths(graph, "catalog_sales", "date_dim")
    assert (result.rule, _fk(result.selected)) == ("preference", SOLD_KEY)
    assert explain(result, graph).warnings == ()

    tree = _tree(graph, "catalog_sales", "date_dim")
    assert (_date(tree).rule, _fk(_date(tree).path)) == ("preference", SOLD_KEY)
    assert explain_tree(tree, graph).codes == frozenset()


def test_outranked_by_the_wording_between_two_tables_is_said_and_does_not_warn(snapshot) -> None:
    graph = _graph(snapshot, routes=(SOLD,))
    result = find_paths(graph, "catalog_sales", "date_dim", evidence=_all(graph, **{SHIP: 0.75, SOLD_KEY: 0.25}), margin=0.25)

    assert (result.rule, _fk(result.selected)) == ("question_evidence", SHIP)
    assert result.preference_outranked == SOLD
    assert result.preference_applied is None and result.preference_not_applied is None
    explanation = explain(result, graph)
    assert explanation.warnings == ()
    assert "the wording of the question outranks it" in explanation.reason.text


def test_outranked_in_a_tree_of_the_two_tables_is_recorded_and_said(snapshot) -> None:
    graph = _graph(snapshot, routes=(SOLD,))
    evidence = _all(graph, **{SHIP: 0.75, SOLD_KEY: 0.25})
    tree = _tree(graph, "catalog_sales", "date_dim", evidence=evidence, margin=0.25)

    assert (_date(tree).rule, _fk(_date(tree).path)) == ("question_evidence", SHIP)
    assert _date(tree).preference_outranked == SOLD
    explanation = explain_tree(tree, graph)
    assert explanation.codes == frozenset()
    assert "the wording of the question outranks it" in explanation.attachments[-1].reason.text
