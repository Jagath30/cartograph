"""Everything a command needs before it can put a question through
retrieval: the calibration, the stored snapshot, and an index that embeds
through the cache (DR-11, IR-14).

Shared by `show_eval --step6` and `show_retrieval`, so that both refuse the
same things in the same words.

THE MARGIN IS NOT AN ARGUMENT. It is read from eval/calibration.json for
the alpha asked for, and an alpha it was not computed for is refused. The
command stops if that file, the stored snapshot and the live warehouse are
not all the same schema: scores from one schema read with thresholds from
another would look like numbers and mean nothing.
"""

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings
from app.core.retriever import Settings
from app.core.snapshot import SchemaSnapshot
from app.shell.embedder import Embedded, EmbeddingKeyMissing, MeteredEmbedder, make_embedder
from app.shell.semantic_index import SemanticIndex
from app.shell.snapshot_store import SnapshotStore, StoredSnapshot, snapshot_hash
from app.shell.vector_cache import CachedEmbedder

EVAL = Path(__file__).resolve().parents[2] / "eval"
CALIBRATION = EVAL / "calibration.json"
CACHE = EVAL / ".cache"


class _NoKey:
    """Stands where the real embedder would when no key is configured. A
    run whose vectors are all cached never reaches it; one that needs a new
    vector gets the usual message."""

    def __init__(self, model: str, missing: EmbeddingKeyMissing) -> None:
        self.model = model
        self._missing = missing

    def embed(self, texts: list[str]) -> Embedded:
        raise self._missing


@dataclass(frozen=True)
class Opened:
    calibration: dict
    stored: StoredSnapshot
    margin: float
    settings: Settings
    # Counts what is embedded now, as against read from the cache.
    paid: MeteredEmbedder
    index: SemanticIndex


def open_retrieval(snapshot: SchemaSnapshot, alpha: float, cut: float, cap: int, name: str) -> Opened:
    """`snapshot` is the live warehouse as just ingested. `name` begins
    every message, so the reader knows which command stopped."""
    settings = get_settings()
    if not CALIBRATION.exists():
        sys.exit(f"{name}: eval/calibration.json is missing. Run: python -m app.calibrate")
    calibration = json.loads(CALIBRATION.read_text())

    stored = SnapshotStore(settings.app_database_url).current()
    hashes = {"live warehouse": snapshot_hash(snapshot), "stored snapshot": stored.hash,
              "calibration": calibration["snapshot_sha256"]}  # fmt: skip
    if len(set(hashes.values())) != 1:
        described = "; ".join(f"{label} {value[:12]}" for label, value in hashes.items())
        sys.exit(
            f"{name}: these are not the same schema: {described}. Run python -m app.ingest_schema, "
            "then python -m app.calibrate."
        )
    if str(alpha) not in calibration["margin"]:
        sys.exit(
            f"{name}: no margin was computed for alpha {alpha}. The grid is "
            f"{', '.join(calibration['margin'])}; a margin is never made up for another value."
        )
    margin = calibration["margin"][str(alpha)]["value"]

    try:
        paid = MeteredEmbedder(make_embedder(settings))
    except EmbeddingKeyMissing as missing:
        paid = MeteredEmbedder(_NoKey(calibration["embedding_model"], missing))
    index = SemanticIndex(settings.app_database_url, CachedEmbedder(paid, CACHE))
    return Opened(calibration, stored, margin, Settings(alpha, cut, cap, margin), paid, index)
