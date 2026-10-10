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

THE LOCAL USER. Until authentication arrives at step 11, every query
belongs to the one user the second migration seeded.
"""

from uuid import UUID

import psycopg

from app.core.trace_document import TRACE_VERSION, TraceDocument

LOCAL_USER_EMAIL = "local@cartograph.invalid"


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

    def load(self, query_id: UUID) -> TraceDocument | None:
        with psycopg.connect(self._dsn) as connection:
            row = connection.execute(
                "select trace_version, body from traces where query_id = %s", (query_id,)
            ).fetchone()
        if row is None:
            return None
        version, body = row
        if version != TRACE_VERSION:
            raise ValueError(f"this build reads trace_version {TRACE_VERSION} and the stored trace is trace_version {version}")
        return TraceDocument.model_validate(body)
