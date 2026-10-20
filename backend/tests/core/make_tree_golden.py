"""Writes tree_golden.json: the sha256 of every tree build_tree makes over
TPC-DS for all pairs of tables and every third triple, without evidence and
with a fixed evidence, from the code as it stands. Run once, before
join_tree gains `chosen`; the test holds join_tree to it after.

    docker compose exec backend python tests/core/make_tree_golden.py
"""

import hashlib
import json
import sys
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tpcds_files import OVERLAY, ddl_snapshot  # noqa: E402

from app.core.graph_builder import build_graph, table_nodes  # noqa: E402
from app.core.join_tree import build_tree  # noqa: E402
from app.core.overlay import apply_overlay, parse_overlay  # noqa: E402

GOLDEN = Path(__file__).resolve().parent / "tree_golden.json"
MARGIN = 0.1


def graph():
    return build_graph(apply_overlay(ddl_snapshot(), parse_overlay(OVERLAY.read_text())))


def evidence_of(g) -> dict[str, float]:
    """A fixed score for every column, from its name: no embedding."""
    columns = [node for node, data in g.nodes(data=True) if data["kind"] != "table"]
    return {c: int(hashlib.sha256(c.encode()).hexdigest()[:6], 16) / 0xFFFFFF for c in columns}


def inputs(g):
    tables = sorted(table_nodes(g))
    return [*combinations(tables, 2), *list(combinations(tables, 3))[::3]]


def digest(tree) -> str:
    return hashlib.sha256(repr(tree).encode()).hexdigest()


def trees(g, **chosen):
    evidence = evidence_of(g)
    found = {}
    for anchors in inputs(g):
        scores = {a: 1.0 - i / 10 for i, a in enumerate(anchors)}
        key = ",".join(anchors)
        found[key] = digest(build_tree(g, anchors, scores, **chosen))
        found[key + "|evidence"] = digest(build_tree(g, anchors, scores, evidence=evidence, margin=MARGIN, **chosen))
    return found


if __name__ == "__main__":
    if GOLDEN.exists():
        sys.exit(f"{GOLDEN.name} exists; nothing is overwritten")
    found = trees(graph())
    with GOLDEN.open("x") as out:
        json.dump(found, out, indent=0, sort_keys=True)
    print(f"{len(found)} trees recorded")
