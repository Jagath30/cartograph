"""What the API calls: a question asked and kept, a stored query read
back or run again, a user's history, and the schema as stored.

Impure (DD-01), and nothing here decides anything about an answer: it
opens the pipeline, hands the question to the orchestrator, and reads the
two stores.

THE PIPELINE IS OPENED ONCE, AT THE FIRST QUESTION, not when the process
starts: the stack must come up with no warehouse, no stored schema and no
key, and say what is missing when it is asked for something that needs
it. Every such case is `NotReady`, with the reason in words; the API
answers 503.

WHOSE IT IS (step 11). Every call but the schema names the signed-in
user: a question is kept under them, and a stored query or a history is
read only as theirs.

A RERUN (item 88) runs a stored answer's SQL again, now: validated again
exactly as an answer's is (FR-18 to FR-20), against the schema as stored
now, and run through a QueryExecutor on the warehouse's read-only role,
with the same timeout and row cap (NFR-07, NFR-08, NFR-11). It never
opens the pipeline, so no model can be called. The rows are returned and
nothing is stored.

TWO QUESTIONS AT ONCE (NFR-06). Opening is guarded by a lock. After that
nothing is shared that a question changes: the orchestrator keeps a
question's state in the call, and the index, the executor and the store
each open a connection of their own for each use.

THE ALLOWANCE (NFR-25, step 12). Given one, a question is admitted only
if app.core.budget says so: this month's spend as the store records it,
the questions running now, and how many the user has asked today. The
check and the count of questions running share a lock of their own, so
two questions asked at once cannot both take the month's last room.
Refused, it is OverAllowance, before the pipeline is opened. A rerun
calls no model and is never checked.

OPENED AT START (step 12, the owner's ruling 3). `warm` opens the
pipeline, and the application calls it in the background once the
server has started, so a host's wake-up includes the opening and the
first question does not. A question that comes first waits on the same
lock. If the opening is not ready, nothing is kept and the next question
tries again, as before.
"""

import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

import psycopg

from app.core.budget import BUDGET_SPENT, Allowance, Refusal, daily_limit_message, day_start, month_start, refusal
from app.core.snapshot import SchemaSnapshot
from app.core.sql_reading import schema_of
from app.core.sql_validator import validate
from app.core.trace_document import TraceDocument
from app.orchestrator import Executor, Kept, Orchestrator
from app.shell.query_executor import Execution, QueryExecutor
from app.shell.embedder import EmbeddingKeyMissing
from app.shell.model_client import ModelKeyMissing
from app.shell.snapshot_store import NoCurrentSnapshot, SnapshotStore, StoredSnapshot
from app.shell.trace_store import Listed, TraceStore


log = logging.getLogger("cartograph.api")


class NotReady(RuntimeError):
    """Something a request needs is not there yet: a key, a stored
    schema, a database."""


class OverAllowance(Exception):
    """The question is not run: the month's budget, or the user's day."""

    def __init__(self, refused: Refusal, message: str) -> None:
        super().__init__(message)
        self.refused = refused
        self.message = message


_UNREACHABLE = "a database the request needs could not be reached"


@dataclass(frozen=True)
class Rerun:
    status: Literal["answered", "validation_failed", "execution_failed"]
    code: str | None = None
    message: str | None = None
    # The rows, in memory only; None when nothing ran.
    execution: Execution | None = None


class NothingToRerun(Exception):
    """The stored answer has no SQL: it was declined, or the model failed."""


def stored_sql(document: TraceDocument) -> str | None:
    """The SQL the answer ran, or, when it did not run, the last SQL the
    model wrote. Whatever it is, it is validated again before it runs."""
    if document.execution is not None:
        return document.execution.sql
    written = [attempt.sql for attempt in (document.generation.attempts if document.generation else []) if attempt.sql]
    return written[-1] if written else None


class QueryService:
    def __init__(
        self, open_orchestrator: Callable[[], Orchestrator], traces: TraceStore, snapshots: SnapshotStore,
        rerun_executor: Executor | None = None, allowance: Allowance | None = None,
    ) -> None:  # fmt: skip
        self._open = open_orchestrator
        self._traces = traces
        self._snapshots = snapshots
        self._rerun = rerun_executor
        self._orchestrator: Orchestrator | None = None
        self._lock = threading.Lock()
        self._allowance = allowance
        # Questions running now, by user.
        self._running: dict[int, int] = {}
        self._admitting = threading.Lock()

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

    def warm(self) -> None:
        """Open the pipeline now, if it can be. Never raises: what was not
        ready is said again to the question that needs it."""
        try:
            self._pipeline()
        except NotReady as missing:
            log.warning("the pipeline was not opened at start: %s", missing)
        except Exception as error:
            log.error("the pipeline was not opened at start: %s", type(error).__name__)

    def ask(self, question: str, user_id: int, at: datetime | None = None) -> Kept:
        with self._admitted(user_id, at or datetime.now(UTC)):
            orchestrator = self._pipeline()
            try:
                return orchestrator.answer_and_keep(question, user_id=user_id)
            except EmbeddingKeyMissing as missing:
                raise NotReady(str(missing)) from None

    @contextmanager
    def _admitted(self, user_id: int, at: datetime) -> Iterator[None]:
        if self._allowance is None:
            yield
            return
        with self._admitting:
            try:
                spent = self._traces.spent_since(month_start(at))
                asked = self._traces.asked_since(user_id, day_start(at))
            except psycopg.OperationalError:
                raise NotReady(_UNREACHABLE) from None
            running = sum(self._running.values())
            said = refusal(spent, running, asked + self._running.get(user_id, 0), self._allowance)
            if said == "budget_spent":
                raise OverAllowance(said, BUDGET_SPENT)
            if said == "daily_limit":
                raise OverAllowance(said, daily_limit_message(self._allowance.daily_questions))
            self._running[user_id] = self._running.get(user_id, 0) + 1
        try:
            yield
        finally:
            with self._admitting:
                self._running[user_id] -= 1
                if not self._running[user_id]:
                    del self._running[user_id]

    def fetch(self, query_id: UUID, owner: int) -> TraceDocument | None:
        try:
            return self._traces.load(query_id, owner=owner)
        except psycopg.OperationalError:
            raise NotReady(_UNREACHABLE) from None

    def rerun(self, query_id: UUID, owner: int) -> Rerun | None:
        """None when the owner has no such query. Raises NothingToRerun when
        the answer holds no SQL."""
        document = self.fetch(query_id, owner)
        if document is None:
            return None
        sql = stored_sql(document)
        if sql is None:
            raise NothingToRerun(query_id)
        _, snapshot = self.schema()
        validation = validate(sql, schema_of(snapshot))
        if not validation.passed:
            said = " ".join(finding.message for finding in validation.findings)
            return Rerun("validation_failed", "validation_failure", f"The SQL was not run: {said}")
        if self._rerun is None:
            raise NotReady("this service was given no executor to run SQL again")
        execution = self._rerun.run(validation.validated)
        if not execution.ok:
            return Rerun("execution_failed", "execution_failure", execution.message, execution)
        return Rerun("answered", execution=execution)

    def history(self, owner: int) -> list[Listed]:
        try:
            return self._traces.history(owner)
        except psycopg.OperationalError:
            raise NotReady(_UNREACHABLE) from None

    def schema(self) -> tuple[StoredSnapshot, SchemaSnapshot]:
        try:
            return self._snapshots.load_current()
        except NoCurrentSnapshot as missing:
            raise NotReady(f"{missing}. Run: python -m app.ingest_schema") from None
        except psycopg.OperationalError:
            raise NotReady(_UNREACHABLE) from None


def make_service(app_database_url: str, warehouse_database_url: str, allowance: Allowance | None = None) -> QueryService:
    """The real one: the pipeline as the commands open it, with a store,
    and for a rerun the executor an answer runs on: the warehouse's
    read-only role, the default row cap. The API gives it the allowance."""
    from app.shell.pipeline_session import APPLICATION_MODE, open_pipeline

    traces = TraceStore(app_database_url)
    return QueryService(
        lambda: open_pipeline("api", "api", store=traces, mode=APPLICATION_MODE).orchestrator,
        traces,
        SnapshotStore(app_database_url),
        QueryExecutor(warehouse_database_url),
        allowance,
    )
