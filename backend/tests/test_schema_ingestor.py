"""SchemaIngestor against the live warehouse (FR-01, FR-02, FR-43).

Needs a loaded warehouse, and skips cleanly without one -- CI has none.
Everything here connects as the SELECT-only role, through the same URL the
application uses.

The numbers are the ones step 2 pinned: 24 tables, 17 primary keys, 102
foreign keys in the catalog -- and since the overlay began declaring what
tpcds_ri.sql omits, 5 more from there. They are asserted as numbers, and
the two sources are asserted separately. An ingestor that read
information_schema would return 24 tables and no keys at all, without an
error, and "the graph matches the snapshot" would still be true of it.
"""

import os
from collections import Counter
from pathlib import Path

import psycopg
import pytest

from app.core.explainer import explain
from app.core.graph_builder import build_graph, column_nodes, foreign_key_edges, table_nodes
from app.core.overlay import parse_overlay
from app.core.path_finder import find_paths
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
    assert len(snapshot.foreign_keys) == 102 + 5


def test_the_keys_come_from_the_catalog_with_no_overlay_to_lean_on(warehouse_dsn) -> None:
    """The blocking correction of step 3, pinned. No overlay is given, so
    every key below was read from pg_catalog or it is not there. Through
    information_schema this role sees no constraints and both numbers
    would be zero."""
    bare = SchemaIngestor(warehouse_dsn).ingest()

    assert len(bare.primary_keys) == 17
    assert len(bare.foreign_keys) == 102
    assert len(bare.foreign_keys) > 0  # said twice on purpose: never an edgeless graph


def test_102_keys_come_from_the_catalog_and_5_from_the_overlay(snapshot) -> None:
    """Catalog wins (FR-43), exercised on real data for the first time. The
    overlay restates the 102 constraints the database declares, and each of
    those stays `catalog`. It also declares 5 the database does not, and
    only those are `overlay`."""
    assert Counter(key.source for key in snapshot.foreign_keys) == {"catalog": 102, "overlay": 5}

    from_catalog = [key for key in snapshot.foreign_keys if key.source == "catalog"]
    from_overlay = [key for key in snapshot.foreign_keys if key.source == "overlay"]
    assert all(key.name and key.note is None for key in from_catalog)
    assert all(key.name is None and key.note for key in from_overlay)
    assert sorted(len(key.from_columns) for key in from_overlay) == [1, 1, 2, 2, 2]


def test_the_five_overlay_edges_are_the_five_declared_by_hand(snapshot) -> None:
    from_overlay = {
        (key.from_table, key.from_columns, key.to_table, key.to_columns)
        for key in snapshot.foreign_keys
        if key.source == "overlay"
    }

    assert from_overlay == {
        ("customer", ("c_last_review_date_sk",), "date_dim", ("d_date_sk",)),
        ("web_page", ("wp_customer_sk",), "customer", ("c_customer_sk",)),
        ("store_returns", ("sr_item_sk", "sr_ticket_number"), "store_sales", ("ss_item_sk", "ss_ticket_number")),
        ("catalog_returns", ("cr_item_sk", "cr_order_number"), "catalog_sales", ("cs_item_sk", "cs_order_number")),
        ("web_returns", ("wr_item_sk", "wr_order_number"), "web_sales", ("ws_item_sk", "ws_order_number")),
    }


def test_the_catalog_declares_exactly_the_generated_relationships(snapshot) -> None:
    """The 102 from tpcds_ri.sql, and none of the hand-declared ones: the
    database was never asked to hold those."""
    relationships = parse_overlay(OVERLAY.read_text()).relationships
    declared = sorted(
        (key.from_table, key.from_columns, key.to_table, key.to_columns)
        for key in snapshot.foreign_keys
        if key.source == "catalog"
    )

    assert declared == sorted(r.identity for r in relationships if not r.note)
    assert not {r.identity for r in relationships if r.note} & set(declared)


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
    assert Counter(data["source"] for _, _, data in edges) == {"catalog": 102, "overlay": 8}
    assert len({data["foreign_key"] for _, _, data in edges}) == 107
    assert sum(1 for _, flag in graph.nodes(data="primary_key") if flag) == 17

    # The demonstration case (SDD figure 3), as edges of the built graph.
    assert graph.has_edge("store_sales.ss_addr_sk", "customer_address.ca_address_sk")
    assert graph.has_edge("store_sales.ss_store_sk", "store.s_store_sk")
    assert graph.has_edge("store_sales.ss_customer_sk", "customer.c_customer_sk")
    assert graph.has_edge("customer.c_current_addr_sk", "customer_address.ca_address_sk")


def test_paths_over_the_live_graph_tie_where_they_should_and_name_the_catalog(snapshot) -> None:
    """Step 4 end to end: ingested from the warehouse, built, searched,
    explained. Both edges of this tie were declared by the database itself,
    and say so."""
    graph = build_graph(snapshot)

    tie = explain(find_paths(graph, "catalog_sales", "customer_address"), graph)
    assert [warning.code for warning in tie.warnings] == ["arbitrary_choice"]
    assert tie.provenance == (("catalog_sales.cs_bill_addr_sk", "customer_address.ca_address_sk", "catalog"),)
    assert tie.chosen.description == (
        "Each catalog sales row has one customer address, through its bill address surrogate key."
    )
    tied = [path for path in tie.alternatives if path.tied_with_chosen]
    assert [path.joins[0].fk_columns for path in tied] == [("cs_ship_addr_sk",)]
    assert tied[0].joins[0].source == "catalog"

    quiet = explain(find_paths(graph, "store_sales", "customer_address"), graph)
    assert quiet.warnings == ()
    assert quiet.reason.text == "96 routes existed; the shortest was used."


def test_a_return_reaches_its_sale_by_an_overlay_edge_and_says_so(snapshot) -> None:
    """The first route on the live warehouse that the database does not
    declare. One join, no warning, and its provenance reads `overlay` in the
    data and in the sentence -- with the reason its author gave."""
    graph = build_graph(snapshot)
    explanation = explain(find_paths(graph, "store_returns", "store_sales"), graph)

    assert explanation.chosen.tables == ("store_returns", "store_sales")
    assert explanation.warnings == ()
    assert explanation.reason.rule == "shortest"
    assert sorted(explanation.provenance) == [
        ("store_returns.sr_item_sk", "store_sales.ss_item_sk", "overlay"),
        ("store_returns.sr_ticket_number", "store_sales.ss_ticket_number", "overlay"),
    ]
    [join] = explanation.chosen.joins
    assert join.constraint is None
    assert "all 287,867 returns match exactly one sale" in join.note
    assert explanation.chosen.description.endswith("(asserted in the overlay, not declared by the database).")
    assert explanation.chosen.description.startswith("Each store returns row has one store sales, through its ")

    # A mixed route: one edge the database declares, one a person asserted.
    mixed = explain(find_paths(graph, "reason", "store_sales"), graph)
    assert mixed.chosen.tables == ("reason", "store_returns", "store_sales")
    assert [join.source for join in mixed.chosen.joins] == ["catalog", "overlay"]


def test_a_superuser_connection_is_refused(warehouse_dsn) -> None:
    """The application database's URL is the one superuser credential this
    process holds. Handed to the ingestor by mistake, it must not be used."""
    superuser_dsn = os.environ.get("APP_DATABASE_URL")
    if not superuser_dsn:
        pytest.skip("APP_DATABASE_URL is not set")

    with pytest.raises(SuperuserRefused):
        SchemaIngestor(superuser_dsn).ingest()
