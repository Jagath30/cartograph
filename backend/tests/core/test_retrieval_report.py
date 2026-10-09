"""What is printed for one located question, on the hand-written fixture
(FR-09, FR-41, DD-11).

`show_eval --step6` and `show_retrieval` both print through
app.retrieval_report. These tests hold the labels in place: a line of the
record that stops being printed is a silent loss, and it happened once.
The `anchors` and `set aside` lines were deleted by accident during an
edit, and run 3 and the grid's 25 reports were first written without them.
"""

import pytest

from app.core.graph_builder import build_graph, column_nodes, table_nodes
from app.core.locate import locate
from app.core.overlay import apply_overlay, parse_overlay
from app.core.retriever import RawScore, Settings, Term
from app.retrieval_report import joins_of, print_retrieval, print_warnings

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
    scores = [RawScore(table, None, semantic.get(table, 0.3), 0.0) for table in table_nodes(graph)]
    for node in column_nodes(graph):
        table, column = node.split(".")
        scores.append(RawScore(table, column, semantic.get(f"{table}__{column}", 0.3), 0.0))
    return tuple(scores)


def _labels(text: str) -> list[str]:
    """The label of every printed line that has one, in order."""
    return [line[4:15].strip() for line in text.splitlines() if line[4:15].strip()]


def _located(graph, cap: int = 5):
    """catalog_sales and customer_address are anchors, tied on two keys;
    "address" could as well mean store, which is set aside; store_sales is
    joined to customer_address and is kept as its partner."""
    question = _scores(
        graph, catalog_sales=1.0, customer_address=0.9, store=0.8, store_sales=0.7, customer__c_customer_sk=0.0
    )
    address = _scores(graph, customer_address=1.0, store=0.9, store_sales=0.85, customer__c_customer_sk=0.0)
    settings = Settings(alpha=1.0, anchor_cut=0.5, anchor_cap=cap, margin=0.25)
    return locate("q", question, ((Term("address", "word"), address),), graph, settings)


def test_every_line_of_the_record_is_printed_in_order(graph, capsys) -> None:
    print_retrieval(_located(graph))
    printed = capsys.readouterr().out

    assert _labels(printed) == [
        "best raw", "terms", "tables", "columns", "anchors", "set aside", "partners", "cap cut",
        "attached", "attached", "attached", "tree",
    ]  # fmt: skip
    assert "anchors    3: catalog_sales 1.000, customer_address 0.900, store_sales 0.700" in printed
    assert 'set aside  store ("address" chose customer_address: 1.000 against 0.900)' in printed
    assert 'partners   store_sales (joined to customer_address, which "address" chose: 1.000 against 0.850)' in printed
    assert "cap cut    none" in printed
    assert "attached   0  catalog_sales: the seed" in printed
    assert "[alphabetical, 2 tied" in printed
    assert "tree       catalog_sales, customer_address, store_sales" in printed


def test_a_partner_that_is_set_aside_for_another_table_is_not_listed_as_kept(graph, capsys) -> None:
    """store_sales is joined to customer_address, which "address" chose,
    and is not joined to catalog_sales, which "order" chose with
    store_sales within the margin. It is set aside, for catalog_sales, so
    the partners line must not say it was kept. Run 2's first report said
    exactly that of question 9, and was wrong."""
    question = _scores(
        graph, catalog_sales=1.0, customer_address=0.9, store_sales=0.7, customer__c_customer_sk=0.0
    )
    address = _scores(graph, customer_address=1.0, store_sales=0.85, customer__c_customer_sk=0.0)
    order = _scores(graph, catalog_sales=1.0, store_sales=0.9, customer__c_customer_sk=0.0)
    terms = ((Term("address", "word"), address), (Term("order", "word"), order))
    print_retrieval(locate("q", question, terms, graph, SETTINGS))
    printed = capsys.readouterr().out

    assert 'set aside  store_sales ("order" chose catalog_sales: 1.000 against 0.900)' in printed
    assert "partners   none" in printed


def test_what_the_cap_cut_is_printed(graph, capsys) -> None:
    print_retrieval(_located(graph, cap=2))
    printed = capsys.readouterr().out
    assert "anchors    2: catalog_sales 1.000, customer_address 0.900" in printed
    assert "cap cut    store_sales" in printed


def test_no_anchors_says_so_and_prints_no_tree(graph, capsys) -> None:
    scores = _scores(graph, store__s_state=1.0, customer__c_customer_sk=0.0)
    print_retrieval(locate("q", scores, (), graph, Settings(alpha=0.0, anchor_cut=0.5, anchor_cap=5, margin=0.25)))
    printed = capsys.readouterr().out
    assert "anchors    0: none" in printed
    assert "tree       none: no table reached the anchor cut" in printed
    assert "attached" not in printed


def test_a_decline_is_printed_with_its_reason(graph, capsys) -> None:
    scores = _scores(graph, store=1.0, catalog_sales=0.9, customer__c_customer_sk=0.0)
    print_retrieval(locate("q", scores, (), graph, SETTINGS, max_joins=2))
    printed = capsys.readouterr().out
    assert "DECLINED" in _labels(printed)
    assert "cannot be answered" in printed


def test_warnings_are_printed_loudly_and_close_calls_quietly(graph, capsys) -> None:
    located = _located(graph)
    print_warnings(located, "; expected: none")
    printed = capsys.readouterr().out

    # Two real warnings: the two address keys tie, and both sales tables
    # reach customer_address by its primary key.
    assert "warnings   arbitrary_choice, many_to_many; expected: none" in printed
    assert "many_to_many: This answer joins" in printed
    assert "arbitrary_choice: I had no basis for this choice." in printed
    assert 'close call "address" in the question could as well mean store.' in printed
    assert "anchor_ambiguity" not in printed


def test_with_nothing_to_say_the_warnings_line_says_none(graph, capsys) -> None:
    scores = _scores(graph, store=1.0, store_sales=0.9, customer__c_customer_sk=0.0)
    print_warnings(locate("q", scores, (), graph, SETTINGS))
    assert capsys.readouterr().out == "    warnings   none\n"


def test_a_join_of_two_columns_is_one_line(graph) -> None:
    edges = frozenset({frozenset({("a.x", "b.x"), ("a.y", "b.y")}), frozenset({("a.z", "c.z")})})
    assert joins_of(edges) == ["a.x = b.x and a.y = b.y", "a.z = c.z"]
