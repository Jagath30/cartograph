"""Locate: the Retriever, the JoinTree and the Explainer in order, on the
hand-written fixture with scores typed here (FR-08 to FR-14, FR-42).

What is tested is the wiring: that what one stage finds reaches the next.
Each stage's own rules are tested in its own file.
"""

import pytest

from app.core.graph_builder import build_graph, column_nodes, table_nodes
from app.core.locate import locate, set_aside
from app.core.overlay import apply_overlay, parse_overlay
from app.core.retriever import RawScore, Settings, Term

NAMING = """
naming:
  prefixes: { cs_: catalog sales, ss_: store sales, c_: customer, ca_: customer address, s_: store }
  words:    { sk: surrogate key, addr: address }
"""

SETTINGS = Settings(alpha=1.0, anchor_cut=0.5, anchor_cap=5, margin=0.25)


@pytest.fixture
def graph(small_snapshot):
    return build_graph(apply_overlay(small_snapshot, parse_overlay(NAMING)))


def _scores(graph, **semantic: float) -> tuple[RawScore, ...]:
    """Every element of the graph, 0.3 unless named. A column is named
    table__column; a table's own row by the table's name. The lowest named
    score should be 0 so that the normalised scores are the typed ones."""
    scores = [RawScore(table, None, semantic.get(table, 0.3), 0.0) for table in table_nodes(graph)]
    for node in column_nodes(graph):
        table, column = node.split(".")
        scores.append(RawScore(table, column, semantic.get(f"{table}__{column}", 0.3), 0.0))
    return tuple(scores)


def test_anchors_become_a_tree_and_the_tree_is_explained(graph) -> None:
    scores = _scores(graph, store=1.0, store_sales=0.9, customer__c_customer_sk=0.0)
    located = locate("q", scores, (), graph, SETTINGS)

    assert located.declined is False
    assert located.retrieval.anchors == ("store", "store_sales")
    assert located.tree.seed == "store"
    assert located.tables == ("store", "store_sales")
    assert located.warning_codes == frozenset()
    assert located.explanation.attachments[1].reason.rule == "only_path"


def test_the_column_scores_reach_the_tree_as_evidence(graph) -> None:
    """The question scores the shipping key far above the billing key, so
    the tie between them is decided and nothing warns. Had the evidence not
    been passed on, the alphabet would have taken billing and warned."""
    scores = _scores(
        graph, catalog_sales=1.0, customer_address=0.9,
        catalog_sales__cs_ship_addr_sk=0.8, catalog_sales__cs_bill_addr_sk=0.0,
    )  # fmt: skip
    located = locate("q", scores, (), graph, SETTINGS)

    attachment = located.tree.attachments[1]
    assert attachment.rule == "question_evidence"
    assert attachment.path.joins[0].fk_columns == ("cs_ship_addr_sk",)
    assert attachment.margin == 0.25
    assert located.warning_codes == frozenset()


def test_without_enough_evidence_the_tie_is_arbitrary_and_warns(graph) -> None:
    scores = _scores(graph, catalog_sales=1.0, customer_address=0.9, customer__c_customer_sk=0.0)
    located = locate("q", scores, (), graph, SETTINGS)
    assert located.tree.attachments[1].rule == "alphabetical"
    assert located.warning_codes == {"arbitrary_choice"}


def test_a_question_that_matches_nothing_well_is_not_declined_for_it(graph) -> None:
    """The floor that once declined such a question was withdrawn. Raw
    similarity of 0.1 everywhere but one table: that table is the anchor."""
    scores = tuple(
        RawScore(s.table, s.column, 0.12 if s.element == "store" else 0.1, 0.0) for s in _scores(graph)
    )
    located = locate("q", scores, (), graph, SETTINGS)
    assert located.declined is False
    assert located.tables == ("store",)
    assert located.retrieval.best_raw == 0.12


def test_anchors_that_cannot_be_connected_decline_the_question(graph) -> None:
    scores = _scores(graph, store=1.0, catalog_sales=0.9, customer__c_customer_sk=0.0)
    located = locate("q", scores, (), graph, SETTINGS, max_joins=2)

    assert (located.declined, located.decline_reason) == (True, "anchors_not_connected")
    assert located.tree.unconnected == ("catalog_sales",)
    assert located.tables == ()
    # The explanation is kept, to say why; its code is not a warning about
    # an answer, because there is no answer.
    assert "cannot be answered" in located.explanation.reason
    assert located.warning_codes == frozenset()


def test_no_table_at_the_cut_is_not_a_decline_and_has_no_tree_tables(graph) -> None:
    scores = _scores(graph, store__s_state=1.0, customer__c_customer_sk=0.0)
    located = locate("q", scores, (), graph, Settings(alpha=0.0, anchor_cut=0.5, anchor_cap=5, margin=0.25))

    assert located.retrieval.anchors == ()
    assert located.declined is False and located.decline_reason is None
    assert located.tables == ()


def test_a_rival_set_aside_and_absent_from_the_tree_warns_anchor_ambiguity(graph) -> None:
    """The question brings in store_sales, customer_address and store. One
    term, "address", is best matched in customer_address and almost as
    well in store; nothing else nominates store. It is set aside, it is
    not in the tree, and the answer says so."""
    question = _scores(graph, store_sales=1.0, customer_address=0.9, store=0.8, customer__c_customer_sk=0.0)
    address = _scores(graph, customer_address=1.0, store=0.9, customer__c_customer_sk=0.0)
    located = locate("q", question, ((Term("address", "word"), address),), graph, SETTINGS)

    assert located.retrieval.anchors == ("store_sales", "customer_address")
    assert located.retrieval.anchor_bound.set_aside_as_rivals == ("store",)
    (aside,) = set_aside(located.retrieval)
    assert (aside.term, aside.chosen, aside.rival) == ("address", "customer_address", "store")
    assert located.tree.ambiguities == (aside,)
    assert located.warning_codes == {"anchor_ambiguity"}


def test_a_rival_that_never_reached_the_cut_is_not_a_choice_and_does_not_warn(graph) -> None:
    """store is within the margin of customer_address for the term, but on
    the whole question it is nowhere near an anchor. Nothing was set aside."""
    question = _scores(graph, store_sales=1.0, customer_address=0.9, store=0.3, customer__c_customer_sk=0.0)
    address = _scores(graph, customer_address=1.0, store=0.9, customer__c_customer_sk=0.0)
    located = locate("q", question, ((Term("address", "word"), address),), graph, SETTINGS)

    assert [rival.rival for rival in located.retrieval.rivals] == ["store"]
    assert set_aside(located.retrieval) == ()
    assert located.warning_codes == frozenset()


def test_the_bounds_are_passed_on(graph) -> None:
    scores = _scores(graph, store=1.0, customer=0.9, customer_address=0.8, customer__c_customer_sk=0.0)
    located = locate("q", scores, (), graph, SETTINGS, subgraph_bound=3)
    assert located.tree.subgraph_bound.limit == 3
    assert located.tree.subgraph_bound.dropped_anchors == ("customer_address",)
