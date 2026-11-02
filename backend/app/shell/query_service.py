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

THE ANSWER CACHE (FR-35, NFR-25, step 14). `answer` is what the API
calls. Given a cache, it opens the pipeline, which knows the snapshot and
the preferences the key needs, and looks the question up first: a hit is
stored under the asker as a new trace (version 4, served_from_cache) and
returned, its rows from the cache. A hit calls no model, costs 0, never
comes to the allowance, so it is served over the month's budget and past
the user's day, and is not counted in that day. A miss is `ask`, exactly
as before, and an answer or a decline it gives is then cached. Without a
cache, or with Redis down, `answer` is `ask`.

OPENED AT START (step 12, the owner's ruling 3). `warm` opens the
pipeline, and the application calls it in the background once the
server has started, so a host's wake-up includes the opening and the
first question does not. A question that comes first waits on the same
lock. If the opening is not ready, nothing is kept and the next question
tries again, as before.
"""

import hashlib
import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

import psycopg

from app.core.answer_cache import cache_key, entry, served
from app.cells import Value, json_rows
from app.core.budget import BUDGET_SPENT, Allowance, Refusal, daily_limit_message, day_start, month_start, refusal
from app.core.snapshot import SchemaSnapshot
from app.core.sql_reading import schema_of
from app.core.sql_validator import validate
from app.core.trace_document import TraceDocument
from app.orchestrator import Executor, Kept, Orchestrator, TraceNotPersisted
from app.shell.answer_cache import AnswerCache
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


@dataclass(frozen=True)
class Asked:
    """What the API returns for a question: the trace as stored, and the
    rows as JSON carries them; None when nothing ran."""

    document: TraceDocument
    rows: list[list[Value]] | None


def rows_of(kept: Kept) -> list[list[Value]] | None:
    execution = kept.trace.execution
    return json_rows(execution.rows) if execution is not None and execution.ok else None


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
        cache: AnswerCache | None = None, pipeline_identity: dict[str, Any] | None = None,
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
        # The answer cache, and what the process was started with that the
        # key holds (app.core.answer_cache). Both or neither.
        if (cache is None) != (pipeline_identity is None):
            raise ValueError("an answer cache needs the pipeline's identity, and the identity a cache")
        self._cache = cache
        self._identity = pipeline_identity

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

    def answer(self, question: str, user_id: int, at: datetime | None = None) -> Asked:
        """`ask`, through the answer cache when there is one."""
        at = at or datetime.now(UTC)
        if self._cache is None:
            kept = self.ask(question, user_id, at)
            return Asked(kept.document, rows_of(kept))
        began = time.monotonic()
        orchestrator = self._pipeline()
        key = cache_key(question, *orchestrator.answer_basis, self._identity)
        cached = self._cache.get(key)
        if cached is not None:
            return self._serve(cached, question, user_id, at, began)
        kept = self.ask(question, user_id, at)
        rows = rows_of(kept)
        cachable = entry(kept.document, rows)
        if cachable is not None:
            self._cache.put(key, cachable)
        return Asked(kept.document, rows)

    def _serve(self, cached, question: str, user_id: int, at: datetime, began: float) -> Asked:
        document = served(cached, uuid4(), user_id, question, at, round((time.monotonic() - began) * 1000))
        try:
            self._traces.save(document)
        except Exception as error:
            log.error("a cached answer's trace was not stored: %s; query %s", type(error).__name__, document.query_id)
            raise TraceNotPersisted(document.query_id) from None
        log.info("answer cache: hit, query %s, %d ms", document.query_id, document.duration_ms)
        return Asked(document, cached.rows)

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


def make_service(
    app_database_url: str, warehouse_database_url: str, allowance: Allowance | None = None, redis_url: str | None = None
) -> QueryService:
    """The real one: the pipeline as the commands open it, with a store,
    and for a rerun the executor an answer runs on: the warehouse's
    read-only role, the default row cap. The API gives it the allowance,
    and Redis for the answer cache; the code fingerprint is read now."""
    from app.config import get_settings
    from app.core.answer_cache import pipeline_identity
    from app.shell.answer_cache import read_fingerprint
    from app.shell.pipeline_session import APPLICATION_MODE, open_pipeline
    from app.shell.retrieval_session import CALIBRATION

    traces = TraceStore(app_database_url)
    cache = identity = None
    if redis_url is not None:
        settings = get_settings()
        calibration = hashlib.sha256(CALIBRATION.read_bytes()).hexdigest() if CALIBRATION.exists() else "none"
        identity = pipeline_identity(
            APPLICATION_MODE, settings.sql_model, settings.sql_model_reasoning_effort, settings.sql_model_temperature,
            settings.sql_model_max_output_tokens, settings.embedding_model, calibration, read_fingerprint(),
        )  # fmt: skip
        cache = AnswerCache.from_url(redis_url)
    return QueryService(
        lambda: open_pipeline("api", "api", store=traces, mode=APPLICATION_MODE).orchestrator,
        traces,
        SnapshotStore(app_database_url),
        QueryExecutor(warehouse_database_url),
        allowance,
        cache,
        identity,
    )
