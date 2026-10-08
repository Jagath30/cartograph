"""GraphBuilder: snapshot in, graph out (FR-03).

Pure (DD-01). No database, no file, no network: everything that affects the
result is in the argument, which is why it can be tested from a snapshot
written by hand.

The graph is a networkx.DiGraph with two kinds of node and two kinds of
edge, each marked by a `kind` attribute:

    node  "store_sales"                kind="table"
    node  "store_sales.ss_store_sk"    kind="column"

    edge  store_sales -> store_sales.ss_store_sk         kind="has_column"
    edge  store_sales.ss_store_sk -> store.s_store_sk    kind="foreign_key"
                                                         source="catalog" | "overlay"

Columns are nodes, so two foreign keys between the same pair of tables --
cs_bill_addr_sk and cs_ship_addr_sk, both to customer_address -- are two
edges between different nodes. Nothing is merged, and no multigraph is
needed.

A foreign key edge points from the referencing column to the referenced
one. Whether a path may walk it backwards is the PathFinder's business
(step 4), not this module's.

Anything inconsistent raises. A foreign key naming a column that is not in
the snapshot would otherwise become an edge to a node that describes
nothing, and nothing downstream would notice (T-01).
"""

import networkx as nx

from app.core.snapshot import SchemaSnapshot

TABLE = "table"
COLUMN = "column"
HAS_COLUMN = "has_column"
FOREIGN_KEY = "foreign_key"


def column_node(table: str, column: str) -> str:
    return f"{table}.{column}"


def build_graph(snapshot: SchemaSnapshot) -> nx.DiGraph:
    graph = nx.DiGraph()

    for table in snapshot.tables:
        if table.name in graph:
            raise ValueError(f"table {table.name} appears twice in the snapshot")
        graph.add_node(
            table.name,
            kind=TABLE,
            name=table.name,
            readable=table.readable,
            description=table.description,
        )

    for column in snapshot.columns:
        node = column_node(column.table, column.name)
        if column.table not in graph:
            raise ValueError(f"column {node} belongs to a table that is not in the snapshot")
        if node in graph:
            raise ValueError(f"column {node} appears twice in the snapshot")
        graph.add_node(
            node,
            kind=COLUMN,
            table=column.table,
            name=column.name,
            data_type=column.data_type,
            nullable=column.nullable,
            primary_key=False,
            readable=column.readable,
            description=column.description,
        )
        graph.add_edge(column.table, node, kind=HAS_COLUMN)

    for key in snapshot.primary_keys:
        for name in key.columns:
            node = column_node(key.table, name)
            if node not in graph:
                raise ValueError(f"primary key on {node}, which is not in the snapshot")
            graph.nodes[node]["primary_key"] = True

    for key in snapshot.foreign_keys:
        if len(key.from_columns) != len(key.to_columns) or not key.from_columns:
            raise ValueError(f"foreign key {key} does not pair its columns one to one")
        for from_name, to_name in zip(key.from_columns, key.to_columns):
            start = column_node(key.from_table, from_name)
            end = column_node(key.to_table, to_name)
            for node in (start, end):
                if node not in graph:
                    raise ValueError(f"foreign key {start} -> {end}: {node} is not in the snapshot")
            if graph.has_edge(start, end):
                raise ValueError(f"foreign key {start} -> {end} appears twice in the snapshot")
            graph.add_edge(start, end, kind=FOREIGN_KEY, source=key.source, constraint=key.name)

    return graph


def table_nodes(graph: nx.DiGraph) -> list[str]:
    return [node for node, kind in graph.nodes(data="kind") if kind == TABLE]


def column_nodes(graph: nx.DiGraph) -> list[str]:
    return [node for node, kind in graph.nodes(data="kind") if kind == COLUMN]


def foreign_key_edges(graph: nx.DiGraph) -> list[tuple[str, str, dict]]:
    """Every foreign key edge as (referencing column, referenced column,
    attributes). The has_column edges are left out."""
    return [(start, end, data) for start, end, data in graph.edges(data=True) if data["kind"] == FOREIGN_KEY]
