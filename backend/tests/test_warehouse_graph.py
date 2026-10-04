"""The graph of the TPC-DS warehouse, checked rather than trusted (T-01).

A wrong or missing edge is silent: nothing errors, queries run, the graph
draws. This file is what stands against that. It has three parts.

  1. The source. The parser reads all of tpcds_ri.sql -- counted a second
     way that shares no code with it -- and the committed overlay is exactly
     what that file says. Skipped where the file has not been fetched: it
     is the TPC's and is not in the repository, so CI has none.
  2. The graph. Built from the overlay the application will actually read,
     not from the parser's output. Edge count, the demonstration case, and
     the tied pairs.
  3. The database. What the loaded warehouse's own catalog declares, against
     the overlay. Skipped when no warehouse is loaded -- CI has none.

Parts 1 and 2 need no database and no network (DD-01).
"""

import os
import re
from collections import defaultdict
from pathlib import Path

import psycopg
import pytest
import yaml

from warehouse import ri

BACKEND = Path(__file__).resolve().parents[1]
OVERLAY = BACKEND / "overlays" / "tpcds.yaml"
SCHEMA = BACKEND / "warehouse" / "tpcds_schema.sql"

# The number this step exists to pin. Not "roughly eighty" (Charter D-10,
# SDD correction 8): counted from the file on 4 October 2026.
EXPECTED_EDGES = 102

FACT_TABLES = {
    "catalog_returns", "catalog_sales", "inventory", "store_returns",
    "store_sales", "web_returns", "web_sales",
}  # fmt: skip

# Table pairs joined by more than one foreign key. Each is a tie at one hop
# that the shortest-path rule cannot separate (DD-21): billing against
# shipping, refunded against returning, opened against closed.
PARALLEL_PAIRS = {
    ("call_center", "date_dim"): {"cc_closed_date_sk", "cc_open_date_sk"},
    ("catalog_page", "date_dim"): {"cp_end_date_sk", "cp_start_date_sk"},
    ("catalog_returns", "customer"): {"cr_refunded_customer_sk", "cr_returning_customer_sk"},
    ("catalog_returns", "customer_address"): {"cr_refunded_addr_sk", "cr_returning_addr_sk"},
    ("catalog_returns", "customer_demographics"): {"cr_refunded_cdemo_sk", "cr_returning_cdemo_sk"},
    ("catalog_returns", "household_demographics"): {"cr_refunded_hdemo_sk", "cr_returning_hdemo_sk"},
    ("catalog_sales", "customer"): {"cs_bill_customer_sk", "cs_ship_customer_sk"},
    ("catalog_sales", "customer_address"): {"cs_bill_addr_sk", "cs_ship_addr_sk"},
    ("catalog_sales", "customer_demographics"): {"cs_bill_cdemo_sk", "cs_ship_cdemo_sk"},
    ("catalog_sales", "date_dim"): {"cs_ship_date_sk", "cs_sold_date_sk"},
    ("catalog_sales", "household_demographics"): {"cs_bill_hdemo_sk", "cs_ship_hdemo_sk"},
    ("customer", "date_dim"): {"c_first_sales_date_sk", "c_first_shipto_date_sk"},
    ("promotion", "date_dim"): {"p_end_date_sk", "p_start_date_sk"},
    ("web_page", "date_dim"): {"wp_access_date_sk", "wp_creation_date_sk"},
    ("web_returns", "customer"): {"wr_refunded_customer_sk", "wr_returning_customer_sk"},
    ("web_returns", "customer_address"): {"wr_refunded_addr_sk", "wr_returning_addr_sk"},
    ("web_returns", "customer_demographics"): {"wr_refunded_cdemo_sk", "wr_returning_cdemo_sk"},
    ("web_returns", "household_demographics"): {"wr_refunded_hdemo_sk", "wr_returning_hdemo_sk"},
    ("web_sales", "customer"): {"ws_bill_customer_sk", "ws_ship_customer_sk"},
    ("web_sales", "customer_address"): {"ws_bill_addr_sk", "ws_ship_addr_sk"},
    ("web_sales", "customer_demographics"): {"ws_bill_cdemo_sk", "ws_ship_cdemo_sk"},
    ("web_sales", "date_dim"): {"ws_ship_date_sk", "ws_sold_date_sk"},
    ("web_sales", "household_demographics"): {"ws_bill_hdemo_sk", "ws_ship_hdemo_sk"},
    ("web_site", "date_dim"): {"web_close_date_sk", "web_open_date_sk"},
}

# An edge is (from table, from column, to table, to column).
Edge = tuple[str, str, str, str]

needs_source = pytest.mark.skipif(
    not ri.RI_FILE.exists(), reason="tpcds_ri.sql has not been fetched -- ./scripts/warehouse.sh fetches it"
)


def overlay_edges() -> list[Edge]:
    """The edges as the application will meet them: read back from the YAML
    with a real parser, split on the dot."""
    document = yaml.safe_load(OVERLAY.read_text())
    edges = []
    for relationship in document["relationships"]:
        from_table, from_column = relationship["from"].split(".")
        to_table, to_column = relationship["to"].split(".")
        edges.append((from_table, from_column, to_table, to_column))
    return edges


def schema_columns() -> dict[str, list[str]]:
    """Table -> its columns, read from the DDL the warehouse is created with."""
    tables: dict[str, list[str]] = {}
    current = None
    for line in SCHEMA.read_text().splitlines():
        if opening := re.fullmatch(r"create table (\w+) \(", line):
            current = tables.setdefault(opening.group(1), [])
        elif column := re.fullmatch(r"    (\w+)\s+\S.*", line):
            current.append(column.group(1))
    return tables


def paths_between(edges: list[Edge], start: str, end: str, max_hops: int) -> list[list[Edge]]:
    """Every simple path from one table to another, as the edges it crosses.

    A join can be walked from either end, so edges are followed in both
    directions; two foreign keys between the same tables are two paths.
    Deliberately small and local to this test -- the real PathFinder is
    step 4, and this must not be the thing it is checked against.
    """
    touching = defaultdict(list)
    for edge in edges:
        touching[edge[0]].append((edge[2], edge))
        touching[edge[2]].append((edge[0], edge))

    found: list[list[Edge]] = []

    def walk(table: str, visited: tuple[str, ...], crossed: list[Edge]) -> None:
        if table == end:
            found.append(crossed)
            return
        if len(crossed) == max_hops:
            return
        for neighbour, edge in touching[table]:
            if neighbour not in visited:
                walk(neighbour, visited + (neighbour,), crossed + [edge])

    walk(start, (start,), [])
    return found


# --------------------------------------------------------------------------
# 1. The source
# --------------------------------------------------------------------------


@needs_source
def test_the_parser_reads_every_active_statement() -> None:
    """Counted twice, by two methods that share nothing. The second is as
    dumb as possible on purpose: lines that begin with `alter table`."""
    text = ri.RI_FILE.read_text()
    counted_by_eye = [line for line in text.splitlines() if line.lower().startswith("alter table")]

    assert len(ri.load()) == len(counted_by_eye) == EXPECTED_EDGES


@needs_source
def test_the_two_statements_left_out_are_the_ones_the_tpc_commented_out() -> None:
    """The file carries 104 statements, not 102. Two were commented out by
    its authors, and both name a column that does not exist. Leaving them
    out is therefore not a choice this project made -- but it is pinned
    here so that it is visible, and so that a third would be noticed."""
    text = ri.RI_FILE.read_text()
    # [ \t], not \s: \s crosses the newline and pairs a bare `--` line with
    # the live statement beneath it.
    commented = re.findall(r"^--[ \t]*alter table (\w+) add constraint \w+ foreign key\s*\((\w+)\)", text, re.MULTILINE)

    assert commented == [("catalog_page", "cp_promo_id"), ("catalog_returns", "cr_ship_date_sk")]
    columns = schema_columns()
    for table, column in commented:
        assert column not in columns[table]


@needs_source
def test_the_overlay_on_disk_is_exactly_what_the_file_says() -> None:
    """The overlay is generated, never edited (DD-16). If the two disagree,
    one of them was touched by hand."""
    keys = ri.load()

    assert OVERLAY.read_text() == ri.render_overlay(keys)
    assert overlay_edges() == [(k.from_table, k.from_column, k.to_table, k.to_column) for k in keys]


def test_the_overlay_holds_only_the_relationships_section() -> None:
    """Naming and preferences arrive at step 3 (SDD correction 3)."""
    assert list(yaml.safe_load(OVERLAY.read_text())) == ["relationships"]


# --------------------------------------------------------------------------
# 2. The graph
# --------------------------------------------------------------------------


def test_edge_count() -> None:
    edges = overlay_edges()

    assert len(edges) == EXPECTED_EDGES
    assert len(set(edges)) == EXPECTED_EDGES


def test_every_edge_joins_two_columns_that_exist() -> None:
    columns = schema_columns()

    assert len(columns) == 24
    for from_table, from_column, to_table, to_column in overlay_edges():
        assert from_column in columns[from_table], f"{from_table}.{from_column}"
        assert to_column in columns[to_table], f"{to_table}.{to_column}"


def test_seven_facts_and_seventeen_dimensions_fall_out_of_the_edges() -> None:
    """Not asserted from a list of names alone: a fact table is one nothing
    points at, and a dimension is one something does. The edges have to
    agree with DR-01's 7 and 17."""
    edges = overlay_edges()
    referenced = {edge[2] for edge in edges}
    never_referenced = set(schema_columns()) - referenced

    assert never_referenced == FACT_TABLES
    assert len(referenced) == 17


def test_the_three_routes_from_a_sale_to_a_region_exist() -> None:
    """The demonstration case (DD-12, figure 3), edge by edge."""
    edges = set(overlay_edges())

    assert ("store_sales", "ss_addr_sk", "customer_address", "ca_address_sk") in edges
    assert ("store_sales", "ss_store_sk", "store", "s_store_sk") in edges
    assert ("store_sales", "ss_customer_sk", "customer", "c_customer_sk") in edges
    assert ("customer", "c_current_addr_sk", "customer_address", "ca_address_sk") in edges

    columns = schema_columns()
    assert "ca_state" in columns["customer_address"]
    assert "s_state" in columns["store"]


def test_the_demonstration_tie_is_two_dimensions_at_one_hop() -> None:
    """What makes the selection arbitrary. A sale reaches a region through
    customer_address and through store, and each is exactly one hop away by
    exactly one foreign key -- so shortest-path has nothing to choose with.

    Note what kind of tie this is: between two destinations, not between two
    routes to the same one. store_sales has no parallel edges at all.
    """
    edges = overlay_edges()

    to_address = paths_between(edges, "store_sales", "customer_address", max_hops=1)
    to_store = paths_between(edges, "store_sales", "store", max_hops=1)

    assert [[edge[1] for edge in path] for path in to_address] == [["ss_addr_sk"]]
    assert [[edge[1] for edge in path] for path in to_store] == [["ss_store_sk"]]
    assert not [pair for pair in PARALLEL_PAIRS if pair[0] == "store_sales"]


def test_the_customer_route_is_the_only_other_way_to_the_address_within_two_hops() -> None:
    """Route C. Same destination as route A, one hop longer, and a different
    meaning: the customer's address on file, not the address on the order."""
    paths = paths_between(overlay_edges(), "store_sales", "customer_address", max_hops=2)

    assert sorted([edge[1] for edge in path] for path in paths) == [
        ["ss_addr_sk"],
        ["ss_customer_sk", "c_current_addr_sk"],
    ]


def test_tied_pairs() -> None:
    """Every pair of tables joined by more than one foreign key -- all of
    them, and nothing else. Twenty-four pairs, two keys each."""
    between = defaultdict(set)
    for from_table, from_column, to_table, _ in overlay_edges():
        between[(from_table, to_table)].add(from_column)
    tied = {pair: columns for pair, columns in between.items() if len(columns) > 1}

    assert tied == PARALLEL_PAIRS
    assert len(tied) == 24


# --------------------------------------------------------------------------
# 3. The database
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def warehouse():
    """A read-only connection to a loaded warehouse, or a skip.

    The same credentials the application uses (FR-01), so this also proves
    the SELECT-only role can read the catalog it will ingest from.
    """
    dsn = os.environ.get("WAREHOUSE_DATABASE_URL")
    if not dsn:
        pytest.skip("WAREHOUSE_DATABASE_URL is not set")
    try:
        connection = psycopg.connect(dsn, connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("the warehouse is not reachable")
    with connection:
        loaded = connection.execute("select count(*) from pg_tables where schemaname = 'public'").fetchone()[0]
        if loaded == 0:
            pytest.skip("the warehouse is empty -- run ./scripts/warehouse.sh")
        yield connection


def catalog_keys(connection: psycopg.Connection, kind: str) -> list[tuple]:
    """Constraints of one kind from pg_catalog, as (table, columns, referenced
    table, referenced columns).

    pg_catalog and not information_schema: the latter shows a role only the
    constraints on tables it owns or may write to, so the read-only role
    sees none there at all.
    """
    return connection.execute(
        """
        select c.conrelid::regclass::text,
               array(select a.attname::text from unnest(c.conkey) with ordinality k(attnum, position)
                     join pg_attribute a on a.attrelid = c.conrelid and a.attnum = k.attnum
                     order by k.position),
               case when c.confrelid <> 0 then c.confrelid::regclass::text end,
               array(select a.attname::text from unnest(c.confkey) with ordinality k(attnum, position)
                     join pg_attribute a on a.attrelid = c.confrelid and a.attnum = k.attnum
                     order by k.position)
        from pg_constraint c
        where c.contype = %s and c.connamespace = 'public'::regnamespace
        """,
        (kind,),
    ).fetchall()


def test_the_warehouse_holds_the_twenty_four_tables_with_rows_in_each(warehouse) -> None:
    tables = [row[0] for row in warehouse.execute("select tablename from pg_tables where schemaname = 'public'")]

    assert sorted(tables) == sorted(schema_columns())
    for table in tables:
        assert warehouse.execute(f"select exists (select 1 from {table})").fetchone()[0], table


def test_the_catalog_declares_the_seventeen_primary_keys(warehouse) -> None:
    declared = {table: columns for table, columns, _, _ in catalog_keys(warehouse, "p")}

    assert len(declared) == 17
    assert declared == {to_table: [to_column] for _, _, to_table, to_column in overlay_edges()}


def test_the_catalog_declares_the_same_foreign_keys_as_the_overlay(warehouse) -> None:
    """FR-02's source against FR-43's. Every constraint applied on this
    warehouse, so the two must be the same set -- which is what keeps the
    founding claim literally true: the graph can be read from what the
    database itself declares."""
    declared = catalog_keys(warehouse, "f")

    assert all(len(columns) == 1 and len(referenced) == 1 for _, columns, _, referenced in declared)
    assert sorted((t, c[0], rt, rc[0]) for t, c, rt, rc in declared) == sorted(overlay_edges())
