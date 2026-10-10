"""What the API calls: a question asked and kept, a stored query read
back, and the schema as stored.

Impure (DD-01), and nothing here decides anything about an answer: it
opens the pipeline, hands the question to the orchestrator, and reads the
two stores.

THE PIPELINE IS OPENED ONCE, AT THE FIRST QUESTION, not when the process
starts: the stack must come up with no warehouse, no stored schema and no
key, and say what is missing when it is asked for something that needs
it. Every such case is `NotReady`, with the reason in words; the API
answers 503.

TWO QUESTIONS AT ONCE (NFR-06). Opening is guarded by a lock. After that
nothing is shared that a question changes: the orchestrator keeps a
question's state in the call, and the index, the executor and the store
each open a connection of their own for each use.
"""

import threading
from collections.abc import Callable
from uuid import UUID

import psycopg

from app.core.snapshot import SchemaSnapshot
from app.core.trace_document import TraceDocument
from app.orchestrator import Kept, Orchestrator
from app.shell.embedder import EmbeddingKeyMissing
from app.shell.model_client import ModelKeyMissing
from app.shell.snapshot_store import NoCurrentSnapshot, SnapshotStore, StoredSnapshot
from app.shell.trace_store import TraceStore


class NotReady(RuntimeError):
    """Something a request needs is not there yet: a key, a stored
    schema, a database."""


_UNREACHABLE = "a database the request needs could not be reached"


class QueryService:
    def __init__(self, open_orchestrator: Callable[[], Orchestrator], traces: TraceStore, snapshots: SnapshotStore) -> None:
        self._open = open_orchestrator
        self._traces = traces
        self._snapshots = snapshots
        self._orchestrator: Orchestrator | None = None
        self._lock = threading.Lock()

    def _pipeline(self) -> Orchestrator:
        with self._lock:
            if self._orchestrator is None:
                try:
                    self._orchestrator = self._open()
                except (ModelKeyMissing, EmbeddingKeyMissing, NoCurrentSnapshot) as missing:
                    raise NotReady(str(missing)) from None
                except SystemExit as stopped:
                    # The commands' own refusals: no calibration, or a
                    # schema that is not the one calibrated on.
                    raise NotReady(str(stopped.code)) from None
                except psycopg.OperationalError:
                    raise NotReady(_UNREACHABLE) from None
            return self._orchestrator

    def ask(self, question: str) -> Kept:
        orchestrator = self._pipeline()
        try:
            return orchestrator.answer_and_keep(question)
        except EmbeddingKeyMissing as missing:
            raise NotReady(str(missing)) from None

    def fetch(self, query_id: UUID) -> TraceDocument | None:
        try:
            return self._traces.load(query_id)
        except psycopg.OperationalError:
            raise NotReady(_UNREACHABLE) from None

    def schema(self) -> tuple[StoredSnapshot, SchemaSnapshot]:
        try:
            return self._snapshots.load_current()
        except NoCurrentSnapshot as missing:
            raise NotReady(f"{missing}. Run: python -m app.ingest_schema") from None
        except psycopg.OperationalError:
            raise NotReady(_UNREACHABLE) from None


def make_service(app_database_url: str) -> QueryService:
    """The real one: the pipeline as the commands open it, with a store."""
    from app.shell.pipeline_session import open_pipeline

    traces = TraceStore(app_database_url)
    return QueryService(
        lambda: open_pipeline("api", "api", store=traces).orchestrator, traces, SnapshotStore(app_database_url)
    )
