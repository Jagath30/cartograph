"""QueryExecutor: validated SQL in, rows out, on the read-only connection
(FR-22 to FR-25, NFR-07, NFR-08, NFR-11).

Impure, and so in shell/ (DD-01). It holds the warehouse's connection
string and no other (DD-02, rule 2).

IT TAKES A ValidatedSql AND NOTHING ELSE. Only validate() can make one, so
there is no way to here that does not pass FR-18 to FR-20 (NFR-08).

THE TEXT SENT IS THE TEXT VALIDATED, BYTE FOR BYTE (rule 3 of step 7). No
LIMIT is added, nothing is wrapped around it, no parameter is bound into
it. That is also why rows are read by a stream and not through a
server-side cursor, which would send `DECLARE ... CURSOR FOR` in front of
the statement. The result says which text it ran.

THE ROW CAP IS A CAP ON WHAT IS FETCHED, NOT ON WHAT IS ASKED. The cap
plus one row is read; if the one more arrives the result is marked
truncated and the query is cancelled, which stops the server sending.

THE TIMEOUT IS THE DATABASE'S (NFR-11). The role carries
statement_timeout; nothing here sets it. It is read at connect, with the
two other things the role must be, and a role with no timeout, or one
that may write, or a superuser, is refused before the statement is sent.

A STREAM USES THE EXTENDED PROTOCOL, in which Postgres itself refuses
text holding more than one statement: a second line behind the validator.

AN ERROR IS AN OUTCOME (DD-04, FR-25): the SQLSTATE and Postgres's own
message for it. The database is ours and its message names our SQL, so
it is kept; this is not a provider's error body.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import psycopg

from app.core.sql_validator import ValidatedSql

DEFAULT_ROW_CAP = 1000

Failure = Literal["timeout", "database_error", "role_refused", "not_reachable"]

_GUARD = (
    "select current_setting('statement_timeout'), current_setting('default_transaction_read_only'), "
    "(select rolsuper from pg_roles where rolname = current_user)"
)


@dataclass(frozen=True)
class Execution:
    # The text that was sent: the very object the validator sealed.
    executed: str
    columns: tuple[str, ...] = ()
    rows: tuple[tuple, ...] = ()
    # More rows existed than the cap; `rows` holds the first `cap` of them.
    truncated: bool = False
    row_cap: int = DEFAULT_ROW_CAP
    duration_ms: int = 0
    # The role's statement_timeout as the database reported it.
    statement_timeout: str | None = None
    failure: Failure | None = None
    sqlstate: str | None = None
    message: str | None = None

    @property
    def ok(self) -> bool:
        return self.failure is None

    @property
    def row_count(self) -> int:
        return len(self.rows)


class QueryExecutor:
    def __init__(
        self,
        warehouse_database_url: str,
        row_cap: int = DEFAULT_ROW_CAP,
        connect: Callable[..., psycopg.Connection] = psycopg.connect,
    ) -> None:
        if row_cap <= 0:
            raise ValueError("the row cap must be positive")
        self._url = warehouse_database_url
        self._cap = row_cap
        self._connect = connect

    def run(self, validated: ValidatedSql) -> Execution:
        if not isinstance(validated, ValidatedSql):
            raise TypeError("the executor runs a ValidatedSql and nothing else (NFR-08)")
        sql = validated.text
        began = time.monotonic()

        def done(**fields) -> Execution:
            return Execution(
                executed=sql, row_cap=self._cap, duration_ms=round((time.monotonic() - began) * 1000), **fields
            )

        try:
            connection = self._connect(self._url, autocommit=True, connect_timeout=5)
        except psycopg.OperationalError:
            return done(failure="not_reachable", message="the warehouse could not be reached")
        try:
            timeout, read_only, superuser = connection.execute(_GUARD).fetchone()
            if timeout in ("0", "0ms", "0s") or read_only != "on" or superuser:
                return done(
                    failure="role_refused",
                    statement_timeout=timeout,
                    message="the warehouse role must be read-only, not a superuser, and carry a "
                    "statement_timeout (NFR-07, NFR-11); this one does not, and nothing was run",
                )
            rows: list[tuple] = []
            truncated = False
            with connection.cursor() as cursor:
                stream = cursor.stream(sql)
                try:
                    for row in stream:
                        if len(rows) == self._cap:
                            truncated = True
                            break
                        rows.append(row)
                finally:
                    # Leaving a stream early cancels the query: the server
                    # stops producing rows nobody will read.
                    stream.close()
                columns = tuple(column.name for column in cursor.description or ())
            return done(columns=columns, rows=tuple(rows), truncated=truncated, statement_timeout=timeout)
        except psycopg.errors.QueryCanceled as error:
            return done(failure="timeout", sqlstate=error.sqlstate, message=_said(error))
        except psycopg.Error as error:
            return done(failure="database_error", sqlstate=error.sqlstate, message=_said(error))
        finally:
            connection.close()


def _said(error: psycopg.Error) -> str:
    primary = error.diag.message_primary if error.diag else None
    return (primary or type(error).__name__)[:500]
