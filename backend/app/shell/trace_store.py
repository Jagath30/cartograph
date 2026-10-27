"""The trace store: the repository for queries and their traces (DD-02
rule 1, DD-07, DD-17, FR-27, NFR-13, DR-08, DR-09).

Impure (DD-01): the only code that touches these two tables.

ONE WRITE. `save` inserts the queries row and the traces row in one
transaction, so there is no partial write to lose: both are there or
neither is. A trace is written once and never updated; a second write of
the same query is refused by the primary key.

The five extracted columns of DD-07 are lifted from the document here, at
write time, and are on `queries`: a history list reads those rows and
never the bodies (DD-17).

THE LOCAL USER. The second migration seeded it; it has no password and
nobody can log in as it. The queries stored before step 11 are its, and
the commands (ask, the evaluation's runners) still ask as it.

WHOSE IT IS (step 11). A load that names an owner finds a query only if
it is that owner's: anyone else's reads as not there, so the API answers
404 and never 403. A history list is one owner's, newest first, read from
`queries` alone and never from the bodies (DD-17).
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import psycopg

from app.core.trace_document import READABLE_VERSIONS, TraceDocument

LOCAL_USER_EMAIL = "local@cartograph.invalid"
# No paging: the newest this many (the owner's ruling 7, a known limit).
HISTORY_LIMIT = 100


@dataclass(frozen=True)
class Listed:
    """One row of a history list."""

    query_id: UUID
    question: str
    created_at: datetime
    outcome: str
    # A choice made without a basis touched the answer (DD-21).
    had_ambiguity: bool


class TraceStore:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def local_user_id(self) -> int:
        with psycopg.connect(self._dsn) as connection:
            row = connection.execute("select id from users where email = %s", (LOCAL_USER_EMAIL,)).fetchone()
        if row is None:
            raise RuntimeError("the application store holds no local user: the migrations are not at head")
        return row[0]

    def save(self, document: TraceDocument) -> None:
        """Both rows, or neither."""
        with psycopg.connect(self._dsn) as connection:
            connection.execute(
                """
                insert into queries (id, user_id, question, created_at, outcome, had_ambiguity,
                                     conformance_result, cost_estimate, duration_ms)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    document.query_id, document.user_id, document.question, document.created_at, document.outcome,
                    document.had_ambiguity, document.conformance_result, document.cost_usd, document.duration_ms,
                ),
            )  # fmt: skip
            connection.execute(
                "insert into traces (query_id, trace_version, body) values (%s, %s, %s::jsonb)",
                (document.query_id, document.trace_version, document.model_dump_json()),
            )

    def load(self, query_id: UUID, owner: int | None = None) -> TraceDocument | None:
        """`owner` None reads any query, as the commands do; the API always
        names one."""
        with psycopg.connect(self._dsn) as connection:
            row = connection.execute(
                """
                select traces.trace_version, traces.body from traces join queries on queries.id = traces.query_id
                where traces.query_id = %s and (%s::bigint is null or queries.user_id = %s::bigint)
                """,
                (query_id, owner, owner),
            ).fetchone()
        if row is None:
            return None
        version, body = row
        if version not in READABLE_VERSIONS:
            read = " and ".join(str(known) for known in READABLE_VERSIONS)
            raise ValueError(f"this build reads trace_version {read} and the stored trace is trace_version {version}")
        return TraceDocument.model_validate(body)

    def history(self, owner: int, limit: int = HISTORY_LIMIT) -> list[Listed]:
        with psycopg.connect(self._dsn) as connection:
            rows = connection.execute(
                """
                select id, question, created_at, outcome, had_ambiguity from queries
                where user_id = %s order by created_at desc, id limit %s
                """,
                (owner, limit),
            ).fetchall()
        return [Listed(*row) for row in rows]

    def spent_since(self, since: datetime) -> float:
        """Every stored query's cost since `since`, in US dollars: what the
        demonstration's monthly budget counts (NFR-25, step 12)."""
        with psycopg.connect(self._dsn) as connection:
            row = connection.execute(
                "select coalesce(sum(cost_estimate), 0) from queries where created_at >= %s", (since,)
            ).fetchone()
        return float(row[0])

    def asked_since(self, owner: int, since: datetime) -> int:
        """How many questions `owner` has asked since `since`."""
        with psycopg.connect(self._dsn) as connection:
            row = connection.execute(
                "select count(*) from queries where user_id = %s and created_at >= %s", (owner, since)
            ).fetchone()
        return row[0]
