"""The committed TPC-DS overlay, checked without a database.

Reads three files that are all in the repository -- the generated overlay,
the hand-written naming source, and the DDL the warehouse is created from --
so every test here runs in CI, which has no warehouse and no tpcds_ri.sql.

That matters most for the edge count. The integration test asserts 102
foreign keys against the live catalog and is skipped in CI; the test below
asserts the same number from the overlay alone, so a push can never go
green on an edgeless graph.
"""

import re
from pathlib import Path

import pytest
import yaml

from app.core.graph_builder import build_graph, foreign_key_edges
from app.core.overlay import apply_overlay, parse_overlay
from app.core.snapshot import Column, SchemaSnapshot, Table
from warehouse import ri

BACKEND = Path(__file__).resolve().parents[2]
OVERLAY = BACKEND / "overlays" / "tpcds.yaml"
NAMING_SOURCE = BACKEND / "overlays" / "tpcds.naming.yaml"
SCHEMA = BACKEND / "warehouse" / "tpcds_schema.sql"


def ddl_snapshot() -> SchemaSnapshot:
    """The 24 tables and their columns, read from the DDL -- with no keys of
    any kind, which is the state DuckDB's output is in (R-09)."""
    tables: list[Table] = []
    columns: list[Column] = []
    for line in SCHEMA.read_text().splitlines():
        if opening := re.fullmatch(r"create table (\w+) \(", line):
            tables.append(Table(opening.group(1)))
        elif column := re.fullmatch(r"    (\w+)\s+(\S.*?),?", line):
            columns.append(Column(tables[-1].name, column.group(1), column.group(2)))
    return SchemaSnapshot(tuple(tables), tuple(columns), (), ())


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


def test_the_generated_file_says_where_naming_is_edited() -> None:
    header = OVERLAY.read_text().split("relationships:")[0]

    assert "GENERATED" in header
    assert f"NAMING EDITS BELONG IN {NAMING_SOURCE.name}" in header
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


def test_from_the_overlay_alone_the_graph_has_all_102_edges(snapshot) -> None:
    """No catalog keys went in, so every edge came from the overlay and says
    so. The mirror image of the live warehouse, where every edge says
    `catalog` -- and the one arrangement CI can check."""
    graph = build_graph(snapshot)
    edges = foreign_key_edges(graph)

    assert len(snapshot.tables) == 24
    assert len(snapshot.columns) == 425
    assert len(edges) == 102
    assert {data["source"] for _, _, data in edges} == {"overlay"}


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

    assert described["store_sales.ss_cdemo_sk"] == "store sales — customer demographics key"
    assert described["store_sales.ss_net_paid_inc_tax"] == "store sales — net paid including tax"
    assert described["date_dim.d_same_day_ly"] == "date — same day last year"
    assert described["date_dim.d_fy_quarter_seq"] == "date — fiscal quarter sequence"
    assert described["call_center.cc_sq_ft"] == "call center — square feet"
    assert described["web_site.web_mkt_desc"] == "web site — market description"
    assert described["inventory.inv_quantity_on_hand"] == "inventory — quantity on hand"
    assert described["customer.c_customer_sk"] == "customer — customer key"
    assert described["customer_address.ca_address_sk"] == "customer address — address key"


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
