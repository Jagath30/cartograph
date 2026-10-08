"""The committed TPC-DS overlay, checked without a database.

Reads three files that are all in the repository -- the generated overlay,
the hand-written naming source, and the DDL the warehouse is created from --
so every test here runs in CI, which has no warehouse and no tpcds_ri.sql.

That matters most for the edge count. The integration test asserts 102
foreign keys against the live catalog and is skipped in CI; the test below
asserts the same number from the overlay alone, so a push can never go
green on an edgeless graph.
"""

import pytest
import yaml
from tpcds_files import NAMING_SOURCE, OVERLAY, RELATIONSHIPS_SOURCE, ddl_snapshot

from app.core.graph_builder import build_graph, foreign_key_edges
from app.core.overlay import Relationship, apply_overlay, parse_overlay
from warehouse import ri


@pytest.fixture(scope="module")
def overlay():
    return parse_overlay(OVERLAY.read_text())


@pytest.fixture(scope="module")
def snapshot(overlay):
    return apply_overlay(ddl_snapshot(), overlay)


# --------------------------------------------------------------------------
# The generated file against its hand-written source
# --------------------------------------------------------------------------


def test_the_overlays_naming_is_the_naming_source_and_the_source_exists() -> None:
    """Drift, in both directions it can happen. Not skipped when the source
    is absent: a missing source means the next regeneration cannot run, and
    the naming in the overlay has become text nobody can safely edit."""
    assert NAMING_SOURCE.exists(), (
        f"{NAMING_SOURCE.name} is missing. It is the only place naming is edited; restore it from git."
    )
    source = NAMING_SOURCE.read_text()
    generated = OVERLAY.read_text()

    assert generated.endswith("\n\n" + ri.naming_section(source)), (
        f"{OVERLAY.name} does not carry the current {NAMING_SOURCE.name}. "
        "Run: python3 backend/warehouse/ri.py write-overlay"
    )
    assert yaml.safe_load(generated)["naming"] == yaml.safe_load(source)["naming"]


def test_the_overlays_hand_declared_relationships_are_their_source_and_the_source_exists() -> None:
    """The same drift test, for the relationships a person declared. Not
    skipped when the source is absent: without it the five edges in the
    overlay are text nobody can safely edit, and the next regeneration
    cannot run."""
    assert RELATIONSHIPS_SOURCE.exists(), (
        f"{RELATIONSHIPS_SOURCE.name} is missing. It is the only place hand-declared relationships "
        "are edited; restore it from git."
    )
    source = RELATIONSHIPS_SOURCE.read_text()
    generated = OVERLAY.read_text()

    assert ri.hand_relationships(source) in generated, (
        f"{OVERLAY.name} does not carry the current {RELATIONSHIPS_SOURCE.name}. "
        "Run: python3 backend/warehouse/ri.py write-overlay"
    )
    declared = yaml.safe_load(source)["relationships"]
    assert yaml.safe_load(generated)["relationships"][-len(declared) :] == declared


def test_the_relationships_source_holds_nothing_but_relationships() -> None:
    assert list(yaml.safe_load(RELATIONSHIPS_SOURCE.read_text())) == ["relationships"]

    with pytest.raises(ValueError, match="only the relationships section belongs here"):
        ri.hand_relationships("relationships:\n  - from: a.x\n    to: b.y\nnaming: {}\n")
    with pytest.raises(ValueError, match="no `relationships:` line"):
        ri.hand_relationships("# nothing here\n")
    with pytest.raises(ValueError, match="no relationships found"):
        ri.hand_relationships("relationships:\n  # all commented out\n")


def test_the_generated_file_says_where_each_hand_written_part_is_edited() -> None:
    header = OVERLAY.read_text().split("relationships:")[0]

    assert "GENERATED" in header
    assert f"NAMING EDITS BELONG IN {NAMING_SOURCE.name}" in header
    assert f"HAND-DECLARED RELATIONSHIPS BELONG IN {RELATIONSHIPS_SOURCE.name}" in header
    assert all(line.startswith("#") or not line for line in header.splitlines())


def test_the_naming_source_holds_nothing_but_naming() -> None:
    assert list(yaml.safe_load(NAMING_SOURCE.read_text())) == ["naming"]

    with pytest.raises(ValueError, match="only the naming section belongs here"):
        ri.naming_section("naming:\n  words: { sk: key }\nrelationships: []\n")
    with pytest.raises(ValueError, match="no `naming:` line"):
        ri.naming_section("# nothing here\n")


# --------------------------------------------------------------------------
# Relationships: the graph has its edges even where the catalog has none
# --------------------------------------------------------------------------


def test_from_the_overlay_alone_the_graph_has_every_edge(snapshot) -> None:
    """No catalog keys went in, so every edge came from the overlay and says
    so -- the one arrangement CI can check. 107 relationships: 102 generated
    from tpcds_ri.sql and 5 declared by hand, three of which are two-column
    keys and so two edges each."""
    graph = build_graph(snapshot)
    edges = foreign_key_edges(graph)

    assert len(snapshot.tables) == 24
    assert len(snapshot.columns) == 425
    assert len(snapshot.foreign_keys) == 102 + 5
    assert len(edges) == 102 + 2 + 3 * 2
    assert len({data["foreign_key"] for _, _, data in edges}) == 107
    assert {data["source"] for _, _, data in edges} == {"overlay"}


def test_the_generated_and_the_hand_declared_are_counted_apart(overlay) -> None:
    """Told apart by where they come from, not by position: a generated
    relationship is one tpcds_ri.sql states, in one column, with no note."""
    by_hand = [r for r in overlay.relationships if r.note]
    generated = [r for r in overlay.relationships if not r.note]

    assert len(generated) == 102
    assert len(by_hand) == 5
    assert all(len(r.from_columns) == 1 for r in generated)
    assert overlay.relationships[:102] == tuple(generated)


def test_the_five_hand_declared_relationships_are_exactly_these(overlay) -> None:
    by_hand = {r.identity for r in overlay.relationships if r.note}

    assert by_hand == {
        ("customer", ("c_last_review_date_sk",), "date_dim", ("d_date_sk",)),
        ("web_page", ("wp_customer_sk",), "customer", ("c_customer_sk",)),
        ("store_returns", ("sr_item_sk", "sr_ticket_number"), "store_sales", ("ss_item_sk", "ss_ticket_number")),
        ("catalog_returns", ("cr_item_sk", "cr_order_number"), "catalog_sales", ("cs_item_sk", "cs_order_number")),
        ("web_returns", ("wr_item_sk", "wr_order_number"), "web_sales", ("ws_item_sk", "ws_order_number")),
    }


def test_each_hand_declared_edge_is_in_the_graph_with_its_note(snapshot) -> None:
    graph = build_graph(snapshot)

    for start, end in [
        ("customer.c_last_review_date_sk", "date_dim.d_date_sk"),
        ("web_page.wp_customer_sk", "customer.c_customer_sk"),
        ("store_returns.sr_item_sk", "store_sales.ss_item_sk"),
        ("store_returns.sr_ticket_number", "store_sales.ss_ticket_number"),
        ("catalog_returns.cr_item_sk", "catalog_sales.cs_item_sk"),
        ("catalog_returns.cr_order_number", "catalog_sales.cs_order_number"),
        ("web_returns.wr_item_sk", "web_sales.ws_item_sk"),
        ("web_returns.wr_order_number", "web_sales.ws_order_number"),
    ]:
        edge = graph.edges[start, end]
        assert edge["source"] == "overlay"
        assert edge["note"].startswith("Not in tpcds_ri.sql")

    pair = graph.edges["store_returns.sr_item_sk", "store_sales.ss_item_sk"]
    other = graph.edges["store_returns.sr_ticket_number", "store_sales.ss_ticket_number"]
    assert pair["foreign_key"] == other["foreign_key"]


def test_the_notes_say_what_each_edge_rests_on_and_do_not_all_say_the_same(overlay) -> None:
    """The evidence differs, so the notes must. The one that rests on a
    name and the specification says so in as many words; a note claiming
    data for it would be the confident wrongness this file exists to avoid."""
    notes = {r.from_columns[0]: r.note for r in overlay.relationships if r.note}

    assert "Rests on the data" in notes["c_last_review_date_sk"]
    assert "96,516" in notes["c_last_review_date_sk"]
    assert "NOT on the data" in notes["wp_customer_sk"]
    assert "cannot tell a real reference from a coincidence" in notes["wp_customer_sk"]
    assert "Rests on the data" not in notes["wp_customer_sk"]
    for column, sales, returns in [
        ("sr_item_sk", "2,880,404", "287,867"),
        ("cr_item_sk", "1,441,548", "144,067"),
        ("wr_item_sk", "719,384", "71,654"),
    ]:
        assert sales in notes[column] and returns in notes[column]
        assert "a sale has at most one return" in notes[column]


def test_a_generated_relationship_was_not_also_declared_by_hand(overlay) -> None:
    identities = [r.identity for r in overlay.relationships]

    assert len(set(identities)) == len(identities) == 107
    assert Relationship("store_sales", ("ss_store_sk",), "store", ("s_store_sk",)) in overlay.relationships


# --------------------------------------------------------------------------
# Naming: applied, complete, and free of dead entries
# --------------------------------------------------------------------------


def test_the_designs_own_examples_read_as_the_design_says(snapshot) -> None:
    """DD-08 and SDD section 08, stage 0."""
    described = {f"{column.table}.{column.name}": column for column in snapshot.columns}

    assert described["store_sales.ss_ext_sales_price"].description == (
        "store sales — extended sales price. Numeric column in table store_sales."
    )
    assert described["date_dim.d_moy"].readable == "date — month of year"
    assert described["date_dim.d_qoy"].readable == "date — quarter of year"
    assert described["customer_address.ca_state"].readable == "customer address — state"
    assert described["web_sales.ws_net_paid"].readable == "web sales — net paid"
    assert described["item.i_category"].readable == "item — category"


def test_some_of_the_harder_names(snapshot) -> None:
    described = {f"{column.table}.{column.name}": column.readable for column in snapshot.columns}

    assert described["store_sales.ss_cdemo_sk"] == "store sales — customer demographics surrogate key"
    assert described["store_sales.ss_net_paid_inc_tax"] == "store sales — net paid including tax"
    assert described["date_dim.d_same_day_ly"] == "date — same day last year"
    assert described["date_dim.d_fy_quarter_seq"] == "date — fiscal quarter sequence"
    assert described["call_center.cc_sq_ft"] == "call center — square feet"
    assert described["web_site.web_mkt_desc"] == "web site — market description"
    assert described["inventory.inv_quantity_on_hand"] == "inventory — quantity on hand"
    assert described["customer_address.ca_address_sk"] == "customer address — address surrogate key"


def test_the_joinable_key_reads_differently_from_the_business_identifier(snapshot) -> None:
    """Every TPC-DS table has both an _sk and an _id, and only the _sk
    joins. "customer key" against "customer identifier" does not say which;
    "surrogate key" does."""
    described = {f"{column.table}.{column.name}": column.readable for column in snapshot.columns}

    assert described["customer.c_customer_sk"] == "customer — customer surrogate key"
    assert described["customer.c_customer_id"] == "customer — customer identifier"
    assert all(readable.endswith("surrogate key") for name, readable in described.items() if name.endswith("_sk"))


def test_every_table_has_exactly_one_prefix_and_every_column_uses_it(overlay, snapshot) -> None:
    """24 tables, 24 prefixes, one each. Checked column by column: each of
    the 425 must be read through the prefix that belongs to its own table,
    so `c_` can never quietly claim a `ca_` or `cc_` column."""
    prefixes = overlay.naming.prefixes
    assert len(prefixes) == 24

    claimed = {}
    for column in snapshot.columns:
        matching = [p for p in prefixes if column.name.startswith(p)]
        assert len(matching) == 1, f"{column.table}.{column.name} matches {matching}"
        assert claimed.setdefault(column.table, matching[0]) == matching[0], column.table
        assert column.readable.startswith(prefixes[matching[0]] + " — "), column.readable

    assert len(claimed) == 24
    assert sorted(claimed.values()) == sorted(prefixes)


def test_tables_read_as_words(snapshot) -> None:
    readable = {table.name: table.readable for table in snapshot.tables}

    assert readable["store_sales"] == "store sales"
    assert readable["date_dim"] == "date dimension"
    assert readable["customer_address"] == "customer address"
    assert all("_" not in name for name in readable.values())


def test_no_entry_in_the_naming_is_dead(overlay, snapshot) -> None:
    """A word that matches no fragment of any name is a typo or a leftover,
    and would otherwise sit in the file looking as if it did something."""
    fragments = {word for column in snapshot.columns for word in column.name.split("_")}
    fragments |= {word for table in snapshot.tables for word in table.name.split("_")}

    assert sorted(set(overlay.naming.words) - fragments) == []
    assert all(word == word.lower() for word in overlay.naming.words)
