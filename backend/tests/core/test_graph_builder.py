"""GraphBuilder, from a snapshot written by hand (FR-03).

No database, no file, no network: the fixture in conftest.py is the whole
input. If these tests ever need a container to pass, GraphBuilder has
stopped being pure (DD-01).
"""

import ast
from dataclasses import replace
from pathlib import Path

import pytest

from app.core.graph_builder import (
    FOREIGN_KEY,
    HAS_COLUMN,
    build_graph,
    column_nodes,
    foreign_key_edges,
    table_nodes,
)
from app.core.snapshot import Column, ForeignKey, PrimaryKey, Table

CORE = Path(__file__).resolve().parents[2] / "app" / "core"


def test_tables_and_columns_are_both_nodes(small_snapshot) -> None:
    graph = build_graph(small_snapshot)

    assert sorted(table_nodes(graph)) == ["catalog_sales", "customer", "customer_address", "store", "store_sales"]
    assert len(column_nodes(graph)) == 13
    assert graph.number_of_nodes() == 5 + 13

    assert graph.nodes["store_sales"]["kind"] == "table"
    assert graph.nodes["store_sales.ss_ext_sales_price"] == {
        "kind": "column",
        "table": "store_sales",
        "name": "ss_ext_sales_price",
        "data_type": "numeric(7,2)",
        "nullable": True,
        "primary_key": False,
        "readable": "",
        "description": "",
    }


def test_every_column_hangs_off_its_table(small_snapshot) -> None:
    graph = build_graph(small_snapshot)
    membership = [(start, end) for start, end, kind in graph.edges(data="kind") if kind == HAS_COLUMN]

    assert len(membership) == 13
    for table, column in membership:
        assert graph.nodes[column]["table"] == table


def test_foreign_keys_are_edges_and_there_are_six_of_them(small_snapshot) -> None:
    """The count is asserted as a number, and not as "the same as the
    snapshot": an edgeless graph built from an edgeless snapshot would pass
    the second and is exactly what must not pass (T-01)."""
    edges = foreign_key_edges(build_graph(small_snapshot))

    assert len(edges) == 6
    assert sorted((start, end) for start, end, _ in edges) == [
        ("catalog_sales.cs_bill_addr_sk", "customer_address.ca_address_sk"),
        ("catalog_sales.cs_ship_addr_sk", "customer_address.ca_address_sk"),
        ("customer.c_current_addr_sk", "customer_address.ca_address_sk"),
        ("store_sales.ss_addr_sk", "customer_address.ca_address_sk"),
        ("store_sales.ss_customer_sk", "customer.c_customer_sk"),
        ("store_sales.ss_store_sk", "store.s_store_sk"),
    ]


def test_an_edge_points_from_the_referencing_column_to_the_referenced_one(small_snapshot) -> None:
    graph = build_graph(small_snapshot)

    assert graph.has_edge("store_sales.ss_store_sk", "store.s_store_sk")
    assert not graph.has_edge("store.s_store_sk", "store_sales.ss_store_sk")


def test_every_edge_says_where_it_came_from(small_snapshot) -> None:
    """FR-43: provenance on every edge, carried through unchanged."""
    graph = build_graph(small_snapshot)
    sources = {start: data["source"] for start, _, data in foreign_key_edges(graph)}

    assert sources["catalog_sales.cs_ship_addr_sk"] == "overlay"
    assert [source for start, source in sources.items() if start != "catalog_sales.cs_ship_addr_sk"] == ["catalog"] * 5

    bill = graph.edges["catalog_sales.cs_bill_addr_sk", "customer_address.ca_address_sk"]
    assert bill == {"kind": FOREIGN_KEY, "source": "catalog", "constraint": "cs_bill_addr_sk_fk"}


def test_two_keys_to_the_same_table_stay_two_edges(small_snapshot) -> None:
    """Billing against shipping address. If these collapsed into one edge,
    step 4 could never report that a choice existed."""
    graph = build_graph(small_snapshot)
    arriving = [start for start, _ in graph.in_edges("customer_address.ca_address_sk")]

    assert "catalog_sales.cs_bill_addr_sk" in arriving
    assert "catalog_sales.cs_ship_addr_sk" in arriving


def test_primary_key_columns_are_marked(small_snapshot) -> None:
    graph = build_graph(small_snapshot)
    marked = sorted(node for node, flag in graph.nodes(data="primary_key") if flag)

    assert marked == ["customer.c_customer_sk", "customer_address.ca_address_sk", "store.s_store_sk"]


def test_readable_names_reach_the_nodes(small_snapshot) -> None:
    """Whatever the snapshot carries, the node carries: step 4's Explainer
    reads its words from here (SDD correction 3)."""
    described = replace(
        small_snapshot,
        tables=tuple(replace(table, readable=f"<{table.name}>") for table in small_snapshot.tables),
        columns=tuple(replace(column, description=f"about {column.name}") for column in small_snapshot.columns),
    )
    graph = build_graph(described)

    assert graph.nodes["store"]["readable"] == "<store>"
    assert graph.nodes["store.s_state"]["description"] == "about s_state"


def test_building_does_not_change_the_snapshot_and_is_repeatable(small_snapshot) -> None:
    first = build_graph(small_snapshot)
    second = build_graph(small_snapshot)

    assert sorted(first.edges(data="kind")) == sorted(second.edges(data="kind"))
    assert dict(first.nodes(data=True)) == dict(second.nodes(data=True))


# --------------------------------------------------------------------------
# What must be refused. Each of these would otherwise build a graph that
# looks fine and is wrong.
# --------------------------------------------------------------------------


def test_a_foreign_key_to_a_column_that_does_not_exist_is_refused(small_snapshot) -> None:
    wrong = ForeignKey("store_sales", ("ss_store_sk",), "store", ("s_shop_sk",), "catalog")
    broken = replace(small_snapshot, foreign_keys=small_snapshot.foreign_keys + (wrong,))

    with pytest.raises(ValueError, match="store.s_shop_sk is not in the snapshot"):
        build_graph(broken)


def test_a_foreign_key_from_a_table_that_does_not_exist_is_refused(small_snapshot) -> None:
    wrong = ForeignKey("web_sales", ("ws_addr_sk",), "customer_address", ("ca_address_sk",), "overlay")
    broken = replace(small_snapshot, foreign_keys=small_snapshot.foreign_keys + (wrong,))

    with pytest.raises(ValueError, match="web_sales.ws_addr_sk is not in the snapshot"):
        build_graph(broken)


def test_the_same_foreign_key_twice_is_refused(small_snapshot) -> None:
    broken = replace(small_snapshot, foreign_keys=small_snapshot.foreign_keys + small_snapshot.foreign_keys[:1])

    with pytest.raises(ValueError, match="appears twice"):
        build_graph(broken)


def test_a_foreign_key_with_unpaired_columns_is_refused(small_snapshot) -> None:
    wrong = ForeignKey("store_sales", ("ss_store_sk", "ss_addr_sk"), "store", ("s_store_sk",), "catalog")
    broken = replace(small_snapshot, foreign_keys=(wrong,))

    with pytest.raises(ValueError, match="one to one"):
        build_graph(broken)


def test_a_duplicated_table_or_column_or_an_orphan_is_refused(small_snapshot) -> None:
    twice_table = replace(small_snapshot, tables=small_snapshot.tables + (Table("store"),))
    twice_column = replace(small_snapshot, columns=small_snapshot.columns + (Column("store", "s_state", "text"),))
    orphan = replace(small_snapshot, columns=small_snapshot.columns + (Column("warehouse", "w_state", "text"),))
    bad_key = replace(small_snapshot, primary_keys=(PrimaryKey("store", ("s_nothing",)),))

    for broken in (twice_table, twice_column, orphan, bad_key):
        with pytest.raises(ValueError):
            build_graph(broken)


# --------------------------------------------------------------------------
# The core stays pure.
# --------------------------------------------------------------------------


def test_nothing_in_the_core_imports_anything_that_does_io() -> None:
    """DD-01, checked rather than trusted. Every import statement in
    app/core is read, and each must come from this short list. A database
    driver, the shell, or the settings appearing here fails the build."""
    allowed = {"app.core", "dataclasses", "networkx", "typing", "yaml"}

    files = sorted(CORE.glob("*.py"))
    assert files, "app/core holds no modules -- this test is looking in the wrong place"
    for path in files:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                assert any(module == name or module.startswith(name + ".") for name in allowed), (
                    f"{path.name} imports {module}"
                )
