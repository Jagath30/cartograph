"""The bodies that cross the API, declared (IR-01 to IR-06, IR-11).

Nothing untyped crosses the boundary: every request and every response is
one of these. The trace is the trace document itself, so what is served
is the type that was stored.

No logic lives here beyond translation: a result's rows into values JSON
can carry.
"""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

from app.core.credentials import MAX_EMAIL, MAX_PASSWORD, MIN_PASSWORD, normalise_email
from app.core.snapshot import SchemaSnapshot
from app.core.trace_document import Outcome, TraceDocument

MAX_QUESTION = 500

# IR-05's codes, as correction 1 of the Design left them, and those of the
# API itself.
ErrorCode = Literal[
    "invalid_request", "not_found", "not_ready", "trace_not_persisted", "internal_error",
    # step 11
    "not_signed_in", "invalid_credentials", "email_taken", "origin_refused", "json_only", "nothing_to_rerun",
]  # fmt: skip

Value = str | int | float | bool | None


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QuestionIn(Body):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUESTION)]


class RegisterIn(Body):
    email: Annotated[str, StringConstraints(max_length=MAX_EMAIL + 64)]
    # Refused here, before anything hashes it (the owner's ruling 4).
    password: Annotated[str, StringConstraints(min_length=MIN_PASSWORD, max_length=MAX_PASSWORD)]

    @field_validator("email")
    @classmethod
    def _email(cls, email: str) -> str:
        return normalise_email(email)


class LoginIn(Body):
    """Only lengths are checked here: a login that is not of an account's
    form fails as any other wrong login does."""

    email: Annotated[str, StringConstraints(max_length=MAX_EMAIL + 64)]
    password: Annotated[str, StringConstraints(max_length=MAX_PASSWORD)]


class AccountOut(Body):
    email: str


class Listed(Body):
    """One row of a history: no trace, no body (DD-17)."""

    query_id: UUID
    question: str
    created_at: datetime
    status: Outcome
    # A choice made without a basis touched the answer (DD-21).
    had_ambiguity: bool


class HistoryOut(Body):
    """IR-08: the caller's newest queries, newest first, at most 100."""

    queries: list[Listed]


class QueryOut(Body):
    """IR-02, IR-03: the query's identifier, its status, the result set as
    columns and rows, and the trace. Every outcome of the pipeline is one
    of these, answered or not (the owner's ruling at stop 1): when it is
    not answered, `code` and `message` are IR-05's."""

    query_id: UUID
    status: Outcome
    code: str | None
    message: str | None
    columns: list[str]
    # None when the rows are not there to give: nothing was run, or this
    # is a stored query read back. The rows are never stored (DR-15).
    rows: list[list[Value]] | None
    row_count: int | None
    truncated: bool | None
    trace: TraceDocument


class Refused(Exception):
    """A request the API says no to, with IR-05's code and a message."""

    def __init__(self, status: int, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class RerunOut(Body):
    """A stored answer's SQL run again (item 88): the rows as of `ran_at`,
    not as of the question. Nothing of it is stored. When it is not
    answered, `code` and `message` are IR-05's."""

    query_id: UUID
    ran_at: datetime
    status: Literal["answered", "validation_failed", "execution_failed"]
    code: str | None
    message: str | None
    columns: list[str]
    rows: list[list[Value]] | None
    row_count: int | None
    truncated: bool | None


class ErrorOut(Body):
    """IR-05: a machine-readable code beside a human-readable message."""

    code: ErrorCode
    message: str
    query_id: UUID | None = None
    detail: list[dict] | None = None


class SchemaColumn(Body):
    name: str
    data_type: str
    readable: str
    primary_key: bool
    foreign_key: bool


class SchemaNode(Body):
    """A table. Its columns are listed inside it and are not nodes (DD-18)."""

    id: str
    readable: str
    columns: list[SchemaColumn]


class SchemaLink(Body):
    """A foreign key, from the table that holds it."""

    id: str
    from_table: str
    from_columns: list[str]
    to_table: str
    to_columns: list[str]
    source: Literal["catalog", "overlay"]
    note: str | None


class SchemaOut(Body):
    """IR-06: the graph as nodes and edges, of the schema as stored."""

    snapshot_id: int
    hash: str
    nodes: list[SchemaNode]
    edges: list[SchemaLink]


def value(raw: object) -> Value:
    """One cell as JSON can carry it. A numeric is given as text, exact; a
    date in ISO form."""
    if raw is None or isinstance(raw, (bool, int, float, str)):
        return raw
    if isinstance(raw, Decimal):
        return str(raw)
    if isinstance(raw, (datetime, date, time)):
        return raw.isoformat()
    return str(raw)


def query_out(document: TraceDocument, rows: tuple[tuple, ...] | None) -> QueryOut:
    execution = document.execution
    ran = execution is not None and execution.failure is None
    return QueryOut(
        query_id=document.query_id,
        status=document.outcome,
        code=document.code,
        message=document.message,
        columns=list(execution.columns) if ran else [],
        rows=[[value(cell) for cell in row] for row in rows] if ran and rows is not None else None,
        row_count=execution.row_count if ran else None,
        truncated=execution.truncated if ran else None,
        trace=document,
    )


def schema_out(snapshot_id: int, digest: str, snapshot: SchemaSnapshot) -> SchemaOut:
    primary = {(key.table, name) for key in snapshot.primary_keys for name in key.columns}
    foreign = {(key.from_table, name) for key in snapshot.foreign_keys for name in key.from_columns}
    return SchemaOut(
        snapshot_id=snapshot_id,
        hash=digest,
        nodes=[
            SchemaNode(
                id=table.name,
                readable=table.readable or table.name,
                columns=[
                    SchemaColumn(
                        name=column.name, data_type=column.data_type, readable=column.readable or column.name,
                        primary_key=(table.name, column.name) in primary, foreign_key=(table.name, column.name) in foreign,
                    )
                    for column in snapshot.columns
                    if column.table == table.name
                ],  # fmt: skip
            )
            for table in snapshot.tables
        ],
        edges=[
            SchemaLink(
                id=" / ".join(f"{key.from_table}.{a}={key.to_table}.{b}" for a, b in zip(key.from_columns, key.to_columns)),
                from_table=key.from_table, from_columns=list(key.from_columns), to_table=key.to_table,
                to_columns=list(key.to_columns), source=key.source, note=key.note,
            )
            for key in snapshot.foreign_keys
        ],  # fmt: skip
    )


def history_out(listed) -> HistoryOut:
    return HistoryOut(
        queries=[
            Listed(query_id=row.query_id, question=row.question, created_at=row.created_at, status=row.outcome,
                   had_ambiguity=row.had_ambiguity)
            for row in listed
        ]
    )  # fmt: skip


def rerun_out(query_id: UUID, ran_at: datetime, rerun) -> RerunOut:
    ran = rerun.status == "answered"
    execution = rerun.execution
    return RerunOut(
        query_id=query_id, ran_at=ran_at, status=rerun.status, code=rerun.code, message=rerun.message,
        columns=list(execution.columns) if ran else [],
        rows=[[value(cell) for cell in row] for row in execution.rows] if ran else None,
        row_count=execution.row_count if ran else None,
        truncated=execution.truncated if ran else None,
    )  # fmt: skip
