"""Compute the margin and the floor from the stored schema, and write them
down.

    docker compose exec backend python -m app.calibrate

Scores every readable name of the schema against every element, as if it
were a question, and from those scores computes

  the margin   one per alpha of the tuning grid (rulings a and c)
  the floor    one (ruling d)

by the methods of app.core.calibration, which were fixed in CHECKPOINTS.md
before any embedding existed. The result is written to
backend/eval/calibration.json, with the hash of the snapshot it was
computed from. That file is committed BEFORE any evaluation question is
embedded, so the history shows these values could not have been chosen
with a result in view; and the runner refuses to use it against any other
snapshot.

THIS COMMAND NEVER READS THE EVALUATION SET. It embeds the schema's
readable names, once; they are cached, so running it again costs nothing
and gives the same file.

Needs OPENAI_API_KEY, and says so if it is absent.
"""

import json
import logging
import sys
from datetime import date
from pathlib import Path

from app.config import get_settings
from app.core.calibration import (
    FLOOR_PERCENTILE,
    MARGIN_PERCENTILE,
    Pseudo,
    floor_bests,
    linked_tables,
    margin_differences,
    percentile,
    sibling_pairs,
)
from app.shell.embedder import EmbeddingKeyMissing, make_embedder
from app.shell.semantic_index import SemanticIndex
from app.shell.snapshot_store import SnapshotStore
from app.shell.vector_cache import CachedEmbedder

EVAL = Path(__file__).resolve().parents[1] / "eval"
CALIBRATION = EVAL / "calibration.json"
CACHE = EVAL / ".cache"

# The tuning grid of CHECKPOINTS.md. The margin is computed at every alpha
# in it, here, before any run.
ALPHAS = (0.0, 0.25, 0.5, 0.75, 1.0)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = get_settings()
    try:
        embedder = CachedEmbedder(make_embedder(settings), CACHE)
    except EmbeddingKeyMissing as missing:
        print(f"NOT CALIBRATED. {missing}", file=sys.stderr)
        return 1

    stored, snapshot = SnapshotStore(settings.app_database_url).load_current()
    index = SemanticIndex(settings.app_database_url, embedder)

    names = [(table.name, table.readable or table.name) for table in snapshot.tables]
    names += [(column.table, column.readable or column.name) for column in snapshot.columns]
    scored = index.score_many([text for _, text in names])
    pseudos = tuple(Pseudo(table, text, scores) for (table, text), scores in zip(names, scored))

    pairs = sibling_pairs(snapshot)
    margins = {}
    for alpha in ALPHAS:
        differences = margin_differences(pseudos, snapshot, pairs, alpha)
        margins[alpha] = differences
        print(
            f"margin at alpha {alpha:<5}{percentile(differences, MARGIN_PERCENTILE):.6f}   "
            f"({len(differences)} differences; median {percentile(differences, 50):.6f}, "
            f"largest {max(differences):.6f})"
        )

    bests = floor_bests(pseudos, linked_tables(snapshot))
    print(
        f"floor                {percentile(bests, FLOOR_PERCENTILE):.6f}   "
        f"({len(bests)} bests; smallest {min(bests):.6f}, 5th percentile {percentile(bests, 5):.6f}, "
        f"95th {percentile(bests, 95):.6f}, largest {max(bests):.6f})"
    )

    record = {
        "computed_on": date.today().isoformat(),
        "method": "app.core.calibration, as fixed in CHECKPOINTS.md before any embedding existed",
        "snapshot_sha256": stored.hash,
        "embedding_model": embedder.model,
        "pseudo_questions": len(pseudos),
        "sibling_pairs": len(pairs),
        "margin_percentile": MARGIN_PERCENTILE,
        "margin": {
            str(alpha): {"value": percentile(values, MARGIN_PERCENTILE), "observations": len(values)}
            for alpha, values in margins.items()
        },
        "floor_percentile": FLOOR_PERCENTILE,
        "floor": {"value": percentile(bests, FLOOR_PERCENTILE), "observations": len(bests)},
    }
    CALIBRATION.write_text(json.dumps(record, indent=2) + "\n")
    print(f"written              backend/eval/{CALIBRATION.name}, for snapshot {stored.hash[:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
