"""Ingest the warehouse, build the graph, print what came out.

    docker compose exec backend python -m app.show_schema

Three lines, and the smoke check (NFR-27) reads them. It is also the
quickest way to look at the result of step 3 with your own eyes.

This is the one place so far where the shell and the core meet: the
ingestor reads, the builder builds, and neither knows about the other. The
orchestrator takes this job over when the pipeline exists (step 7).
"""

from collections import Counter

from app.config import get_settings
from app.core.graph_builder import build_graph, foreign_key_edges
from app.shell.schema_ingestor import SchemaIngestor


def main() -> None:
    settings = get_settings()
    snapshot = SchemaIngestor(settings.warehouse_database_url, settings.warehouse_overlay_path).ingest()
    graph = build_graph(snapshot)

    sources = Counter(key.source for key in snapshot.foreign_keys)
    print(
        f"snapshot  {len(snapshot.tables)} tables, {len(snapshot.columns)} columns, "
        f"{len(snapshot.primary_keys)} primary keys, {len(snapshot.foreign_keys)} foreign keys "
        f"({sources['catalog']} catalog, {sources['overlay']} overlay)"
    )
    print(
        f"graph     {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges, "
        f"of which {len(foreign_key_edges(graph))} foreign key edges for {len(snapshot.foreign_keys)} foreign keys"
    )
    if snapshot.columns:
        widest = max(snapshot.columns, key=lambda column: len(column.name))
        print(f"naming    {widest.table}.{widest.name}: {widest.description}")


if __name__ == "__main__":
    main()
