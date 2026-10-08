"""Read the warehouse's schema, store it, and embed it.

    docker compose exec backend python -m app.ingest_schema

Three steps, each reported:

  ingest   the catalog, as the SELECT-only role, with the overlay applied
  store    as a snapshot in the application store; added if it is new,
           left alone if the store already holds it (DD-17)
  embed    every element that has no vector yet (DR-11). An unchanged
           schema therefore costs nothing the second time.

Embedding needs OPENAI_API_KEY. Without it the snapshot is still stored,
and the command then stops with a message saying what to set; it exits 1
so that nothing downstream mistakes a stored schema for an embedded one.

No logic lives here: ingest, store, embed (shell), print.
"""

import logging
import sys

from app.config import get_settings
from app.shell.embedder import EmbeddingKeyMissing, make_embedder
from app.shell.schema_ingestor import SchemaIngestor
from app.shell.semantic_index import SemanticIndex
from app.shell.snapshot_store import SnapshotStore


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = get_settings()

    snapshot = SchemaIngestor(settings.warehouse_database_url, settings.warehouse_overlay_path).ingest()
    print(
        f"{'ingested':<10}{len(snapshot.tables)} tables, {len(snapshot.columns)} columns, "
        f"{len(snapshot.foreign_keys)} foreign keys"
    )

    stored = SnapshotStore(settings.app_database_url).save(snapshot, settings.warehouse_overlay_path or "catalog only")
    print(f"{'stored':<10}snapshot {stored.id}, {'new' if stored.created else 'already held'}; sha256 {stored.hash}")

    try:
        embedder = make_embedder(settings)
    except EmbeddingKeyMissing as missing:
        print(f"{'embedded':<10}NOT EMBEDDED. {missing}", file=sys.stderr)
        return 1

    report = SemanticIndex(settings.app_database_url, embedder).embed_schema(stored.id)
    print(
        f"{'embedded':<10}{report.embedded} elements now, {report.already} already; model {report.model}; "
        f"{report.tokens} tokens, ${report.cost_usd:.6f}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
