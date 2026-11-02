"""The re-ingest endpoint's one read (IR-07, FR-05, step 14; the owner's
ruling 2). Impure (DD-01).

IT NEVER STORES ANYTHING, AND CALLS NO MODEL AND NO EMBEDDER. It reads the
live warehouse's catalog, as the SELECT-only role, with the overlay
applied, and computes the snapshot's hash. That hash is compared with the
one the frozen calibration was made on (eval/calibration.json):

  the same      nothing has changed that retrieval is calibrated on, and
                the endpoint says "unchanged"
  different     a changed schema. It is refused, not served: the
                calibration is bound to one hash, and a new one is an
                offline step the owner runs (python -m app.ingest_schema,
                then python -m app.calibrate), never this endpoint.

So FR-05 is met only in part: a changed schema is detected and refused,
not stored and served.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.shell.retrieval_session import CALIBRATION
from app.shell.schema_ingestor import SchemaIngestor
from app.shell.snapshot_store import snapshot_hash


@dataclass(frozen=True)
class SchemaRead:
    live: str
    calibrated: str

    @property
    def unchanged(self) -> bool:
        return self.live == self.calibrated


def read_schema(settings: Settings, calibration: Path = CALIBRATION) -> SchemaRead:
    snapshot = SchemaIngestor(settings.warehouse_database_url, settings.warehouse_overlay_path).ingest()
    calibrated = json.loads(calibration.read_text())["snapshot_sha256"]
    return SchemaRead(snapshot_hash(snapshot), calibrated)
