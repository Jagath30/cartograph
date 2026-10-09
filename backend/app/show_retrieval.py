"""Put one question of your own through retrieval and the join tree.

    docker compose exec backend python -m app.show_retrieval "How much did each store sell last year?"
    docker compose exec backend python -m app.show_retrieval "..." --alpha 0.25 --cut 0.7

What the question's words matched, which tables became anchors and which
were set aside or cut, the tree and how each anchor was attached, the joins
handed on, every warning, and the close calls (FR-09, FR-41, DD-11). The
same block `python -m app.show_eval --step6` prints for each question of
the evaluation set, without a verdict: nothing here is judged.

A NEW QUESTION IS EMBEDDED, and that is a paid call: the question and each
of its terms, a few dozen tokens. It needs OPENAI_API_KEY; without it the
command says what to set. Vectors are cached, so the same question asked
again costs nothing, and the last line says what this run embedded.

The margin is not an argument: it is read from eval/calibration.json for
the alpha asked for.

No logic lives here: ingest and score (shell), locate (core), print.
"""

import argparse
import logging
import sys
import time

from app.config import get_settings
from app.core.graph_builder import build_graph
from app.core.join_tree import DEFAULT_SUBGRAPH_BOUND, NO_ANCHORS
from app.core.locate import locate
from app.core.path_finder import DEFAULT_MAX_JOINS
from app.retrieval_report import joins_of, line, print_retrieval, print_warnings
from app.shell.embedder import EmbeddingKeyMissing
from app.shell.retrieval_session import open_retrieval
from app.shell.schema_ingestor import SchemaIngestor


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="One question through retrieval and the join tree.")
    parser.add_argument("question")
    parser.add_argument("--alpha", type=float, default=0.5, help="weight of the semantic score (DD-09); default 0.5")
    parser.add_argument("--cut", type=float, default=0.5, help="anchor cut; default 0.5")
    parser.add_argument("--cap", type=int, default=5, help="anchor cap; default 5")
    parser.add_argument("--bound", type=int, default=DEFAULT_SUBGRAPH_BOUND, help="subgraph bound (DD-11); default 10")
    arguments = parser.parse_args(argv)
    if not arguments.question.strip():
        parser.exit(2, "show_retrieval: the question is empty\n")

    # The embedder logs one line per paid call, with tokens and cost
    # (NFR-14). httpx's own request line is turned down, as in ingest_schema.
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    settings = get_settings()
    snapshot = SchemaIngestor(settings.warehouse_database_url, settings.warehouse_overlay_path).ingest()
    graph = build_graph(snapshot)
    opened = open_retrieval(snapshot, arguments.alpha, arguments.cut, arguments.cap, "show_retrieval")

    began = time.monotonic()
    try:
        scored = opened.index.score_question(arguments.question)
    except EmbeddingKeyMissing as missing:
        print(f"show_retrieval: this question is not in the cache and cannot be embedded. {missing}", file=sys.stderr)
        return 1
    scored_at = time.monotonic()
    located = locate(
        arguments.question, scored.scores, scored.terms, graph, opened.settings,
        max_joins=DEFAULT_MAX_JOINS, subgraph_bound=arguments.bound,
    )  # fmt: skip
    located_at = time.monotonic()

    print(f"{'question':<12}{arguments.question}")
    print(f"{'settings':<12}alpha {arguments.alpha}; anchor cut {arguments.cut}, cap {arguments.cap}; "
          f"subgraph bound {arguments.bound}; routes of up to {DEFAULT_MAX_JOINS} joins; "
          f"margin {opened.margin:.6f} (from eval/calibration.json, not tuned)")  # fmt: skip
    print(f"{'snapshot':<12}{opened.stored.id}, sha256 {opened.stored.hash}; embedded with "
          f"{opened.stored.embedding_model}")  # fmt: skip
    print()

    print_retrieval(located)
    tree = located.tree
    if not located.declined and tree.decline_reason != NO_ANCHORS:
        for position, join in enumerate(joins_of(tree.edges)):
            line("joins" if position == 0 else "", join)
        if not tree.edges:
            line("joins", "none")
        line("subgraph", ", ".join(tree.subgraph) + "  (the tree, then tables on tied alternatives, within the bound)")
    print_warnings(located)

    print()
    print(f"{'timing':<12}scoring {(scored_at - began) * 1000:.0f} ms, of which external embedding calls "
          f"{opened.paid.seconds * 1000:.0f} ms; retrieval and tree {(located_at - scored_at) * 1000:.0f} ms")  # fmt: skip
    print(f"{'cost':<12}embedded now: {opened.paid.texts} texts, {opened.paid.tokens} tokens, "
          f"${opened.paid.cost_usd:.8f}; everything else came from the cache")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
