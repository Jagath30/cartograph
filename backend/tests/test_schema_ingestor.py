"""SchemaIngestor against the live warehouse (FR-01, FR-02, FR-43).

Needs a loaded warehouse, and skips cleanly without one -- CI has none.
Everything here connects as the SELECT-only role, through the same URL the
application uses.

The numbers are the ones step 2 pinned: 24 tables, 17 primary keys, 102
foreign keys. They are asserted as numbers. An ingestor that read
information_schema would return 24 tables and no keys at all, without an
error, and "the graph matches the snapshot" would still be true of it.
"""

import os
from collections import Counter
from pathlib import Path

import psycopg
import pytest

from app.core.graph_builder import build_graph, column_nodes, foreign_key_edges, table_nodes
from app.core.overlay import parse_overlay
from app.shell.schema_ingestor import SchemaIngestor, SuperuserRefused

OVERLAY = Path(__file__).resolve().parents[1] / "overlays" / "tpcds.yaml"


@pytest.fixture(scope="module")
def warehouse_dsn() -> str:
    dsn = os.environ.get("WAREHOUSE_DATABASE_URL")
    if not dsn:
        pytest.skip("WAREHOUSE_DATABASE_URL is not set")
    try:
        with psycopg.connect(dsn, connect_timeout=3) as connection:
            loaded = connection.execute("select count(*) from pg_tables where schemaname = 'public'").fetchone()[0]
    except psycopg.OperationalError:
        pytest.skip("the warehouse is not reachable")
    if loaded == 0:
        pytest.skip("the warehouse is empty -- run ./scripts/warehouse.sh")
    return dsn


@pytest.fixture(scope="module")
def snapshot(warehouse_dsn):
    return SchemaIngestor(warehouse_dsn, OVERLAY).ingest()


def test_the_snapshot_holds_the_whole_warehouse(snapshot) -> None:
    assert len(snapshot.tables) == 24
    assert len(snapshot.columns) == 425
    assert len(snapshot.primary_keys) == 17
    assert len(snapshot.foreign_keys) == 102


def test_the_keys_come_from_the_catalog_with_no_overlay_to_lean_on(warehouse_dsn) -> None:
    """The blocking correction of step 3, pinned. No overlay is given, so
    every key below was read from pg_catalog or it is not there. Through
    information_schema this role sees no constraints and both numbers
    would be zero."""
    bare = SchemaIngestor(warehouse_dsn).ingest()

    assert len(bare.primary_keys) == 17
    assert len(bare.foreign_keys) == 102
    assert len(bare.foreign_keys) > 0  # said twice on purpose: never an edgeless graph


def test_every_foreign_key_is_marked_as_coming_from_the_catalog(snapshot) -> None:
    """Catalog wins (FR-43). All 102 constraints applied on this warehouse,
    so the overlay restates what the catalog declares and adds nothing."""
    assert Counter(key.source for key in snapshot.foreign_keys) == {"catalog": 102}
    assert all(key.name for key in snapshot.foreign_keys)


def test_the_catalog_declares_exactly_the_overlays_relationships(snapshot) -> None:
    declared = sorted(
        (key.from_table, *key.from_columns, key.to_table, *key.to_columns) for key in snapshot.foreign_keys
    )
    in_overlay = sorted(
        (r.from_table, r.from_column, r.to_table, r.to_column)
        for r in parse_overlay(OVERLAY.read_text()).relationships
    )

    assert declared == in_overlay


def test_columns_arrive_in_order_with_their_types(snapshot) -> None:
    store_sales = [column for column in snapshot.columns if column.table == "store_sales"]

    assert len(store_sales) == 23
    assert [column.name for column in store_sales[:3]] == ["ss_sold_date_sk", "ss_sold_time_sk", "ss_item_sk"]
    price = next(column for column in store_sales if column.name == "ss_ext_sales_price")
    assert price.data_type == "numeric(7,2)"
    assert price.nullable is True

    # A primary key column is the one place NOT NULL appears in this warehouse.
    assert sum(not column.nullable for column in snapshot.columns) == 17


def test_the_naming_overlay_reaches_the_ingested_snapshot(snapshot) -> None:
    """DD-08 end to end: file on disk -> ingestor -> snapshot. The three
    readings are the ones SDD section 08 narrates at stage 0."""
    described = {f"{column.table}.{column.name}": column for column in snapshot.columns}

    assert described["store_sales.ss_ext_sales_price"].description == (
        "store sales — extended sales price. Numeric column in table store_sales."
    )
    assert described["date_dim.d_qoy"].readable == "date — quarter of year"
    assert described["customer_address.ca_state"].readable == "customer address — state"

    # Every column, not three chosen ones: each is read through its table's prefix.
    assert all(" — " in column.readable for column in snapshot.columns)


def test_the_graph_built_from_it_has_the_edges(snapshot) -> None:
    """Shell and core together: ingest, then build. The step's headline."""
    graph = build_graph(snapshot)
    edges = foreign_key_edges(graph)

    assert len(table_nodes(graph)) == 24
    assert len(column_nodes(graph)) == 425
    assert len(edges) == 102
    assert {data["source"] for _, _, data in edges} == {"catalog"}
    assert sum(1 for _, flag in graph.nodes(data="primary_key") if flag) == 17

    # The demonstration case (SDD figure 3), as edges of the built graph.
    assert graph.has_edge("store_sales.ss_addr_sk", "customer_address.ca_address_sk")
    assert graph.has_edge("store_sales.ss_store_sk", "store.s_store_sk")
    assert graph.has_edge("store_sales.ss_customer_sk", "customer.c_customer_sk")
    assert graph.has_edge("customer.c_current_addr_sk", "customer_address.ca_address_sk")


def test_a_superuser_connection_is_refused(warehouse_dsn) -> None:
    """The application database's URL is the one superuser credential this
    process holds. Handed to the ingestor by mistake, it must not be used."""
    superuser_dsn = os.environ.get("APP_DATABASE_URL")
    if not superuser_dsn:
        pytest.skip("APP_DATABASE_URL is not set")

    with pytest.raises(SuperuserRefused):
        SchemaIngestor(superuser_dsn).ingest()
