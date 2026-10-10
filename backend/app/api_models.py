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

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.core.snapshot import SchemaSnapshot
from app.core.trace_document import Outcome, TraceDocument

MAX_QUESTION = 500

# IR-05's codes, as correction 1 of the Design left them, and those of the
# API itself.
ErrorCode = Literal["invalid_request", "not_found", "not_ready", "trace_not_persisted", "internal_error"]

Value = str | int | float | bool | None


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QuestionIn(Body):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUESTION)]


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
