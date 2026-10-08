"""Print the join paths between two tables of the live warehouse.

    docker compose exec backend python -m app.show_paths catalog_sales customer_address
    docker compose exec backend python -m app.show_paths store_sales customer_address --all
    docker compose exec backend python -m app.show_paths income_band inventory --max-joins 4

The ambiguity the project is about, visible before any model is involved
(SDD section 09, step 4). Shows the chosen path, why it was chosen, every
warning, and the routes tied with it. `--all` lists every alternative;
without it the longer ones are only counted.

No logic lives here: ingest (shell), build, find, explain (core), print.
"""

import argparse
from collections import Counter

from app.config import get_settings
from app.core.explainer import ExplainedPath, explain
from app.core.graph_builder import build_graph
from app.core.path_finder import DEFAULT_MAX_JOINS, find_paths
from app.shell.schema_ingestor import SchemaIngestor


def _show(label: str, path: ExplainedPath) -> None:
    print(f"{label}  {' -> '.join(path.tables)}   [{path.length} join{'s' if path.length != 1 else ''}]")
    print(f"          {path.description}")
    for join in path.joins:
        for fk, pk in zip(join.fk_columns, join.pk_columns):
            print(f"          {join.fk_table}.{fk} = {join.pk_table}.{pk}   source: {join.source}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Join paths between two tables of the warehouse.")
    parser.add_argument("start")
    parser.add_argument("end")
    parser.add_argument("--max-joins", type=int, default=DEFAULT_MAX_JOINS)
    parser.add_argument("--all", action="store_true", help="list every alternative, not only the tied ones")
    arguments = parser.parse_args()

    settings = get_settings()
    snapshot = SchemaIngestor(settings.warehouse_database_url, settings.warehouse_overlay_path).ingest()
    graph = build_graph(snapshot)
    try:
        result = find_paths(graph, arguments.start, arguments.end, arguments.max_joins)
    except ValueError as problem:
        parser.exit(2, f"show_paths: {problem}\n")
    explanation = explain(result, graph)

    by_length = Counter(path.length for path in result.discovered)
    found = ", ".join(f"{count} of {length} join{'s' if length != 1 else ''}" for length, count in sorted(by_length.items()))
    print(f"found     {len(result.discovered)} routes within {result.max_joins} joins" + (f": {found}" if found else ""))

    if explanation.chosen is not None:
        _show("chosen  ", explanation.chosen)
    print(f"reason    {explanation.reason.text}")
    for warning in explanation.warnings:
        print(f"WARNING   {warning.code}: {warning.text}")

    tied = [path for path in explanation.alternatives if path.tied_with_chosen]
    others = [path for path in explanation.alternatives if not path.tied_with_chosen]
    for path in tied:
        _show("tied    ", path)
    if arguments.all:
        for path in others:
            _show("longer  ", path)
    elif others:
        print(f"          and {len(others)} longer alternatives; --all lists them")


if __name__ == "__main__":
    main()
