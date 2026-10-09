"""QueryExecutor against the live warehouse (FR-22 to FR-25, NFR-07,
NFR-08, NFR-11, rule 3 of step 7).

Needs a loaded warehouse and skips cleanly without one. Everything
connects as the SELECT-only role, through the URL the application uses.
"""

import os
import time

import psycopg
import pytest

from app.core import sql_validator
from app.core.sql_reading import schema_of
from app.core.sql_validator import ValidatedSql, validate
from app.shell.query_executor import DEFAULT_ROW_CAP, QueryExecutor
from tests.core.tpcds_files import ddl_snapshot

SCHEMA = schema_of(ddl_snapshot())


@pytest.fixture(scope="module")
def warehouse_dsn() -> str:
    dsn = os.environ.get("WAREHOUSE_DATABASE_URL")
    if not dsn:
        pytest.skip("WAREHOUSE_DATABASE_URL is not set")
    try:
        with psycopg.connect(dsn, connect_timeout=3) as connection:
            loaded = connection.execute("select count(*) from pg_tables where schemaname = 'public'").fetchone()[0]
    except psycopg.OperationalError:
        pytest.skip("the warehouse is not reachable")
    if loaded == 0:
        pytest.skip("the warehouse is empty -- run ./scripts/warehouse.sh")
    return dsn


def valid(sql: str) -> ValidatedSql:
    validation = validate(sql, SCHEMA)
    assert validation.passed, validation.findings
    return validation.validated


def forged(sql: str) -> ValidatedSql:
    """What no application code can do: a ValidatedSql that validate() did
    not make. Only to show what the database does when the first line of
    defence is taken away."""
    return ValidatedSql(sql, sql_validator._SEAL)


# --------------------------------------------------------------------------
# No database needed
# --------------------------------------------------------------------------


class _Spy:
    """Stands where a connection would, and keeps the statement it is handed."""

    def __init__(self, guard=("30s", "on", False), fail: Exception | None = None) -> None:
        self.guard, self.fail = guard, fail
        self.streamed: list = []
        self.closed = False

    def __call__(self, url, **options):
        self.url, self.options = url, options
        return self

    def execute(self, statement):
        self.guard_statement = statement
        return self

    def fetchone(self):
        return self.guard

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    description = ()

    def stream(self, statement, *args, **kwargs):
        assert not args and not kwargs, "nothing is bound into the statement"
        self.streamed.append(statement)
        if self.fail:
            raise self.fail
        return (row for row in ())

    def close(self):
        self.closed = True


def test_the_statement_handed_to_the_driver_is_the_validated_text_itself() -> None:
    """Rule 3. Not an equal string: the same object, with its odd spacing,
    its comment, its trailing semicolon and its percent signs."""
    validated = valid("select  s_store_name\n\tFROM store -- which\nWHERE s_store_name LIKE '%a%' ;  ")
    spy = _Spy()
    execution = QueryExecutor("postgresql://x/wh", connect=spy).run(validated)

    assert len(spy.streamed) == 1 and spy.streamed[0] is validated.text
    assert execution.executed is validated.text and execution.ok
    assert spy.closed


def test_the_executor_takes_a_validated_sql_and_nothing_else() -> None:
    spy = _Spy()
    executor = QueryExecutor("postgresql://x/wh", connect=spy)
    for not_validated in ("SELECT 1", b"SELECT 1", None, object()):
        with pytest.raises(TypeError, match="ValidatedSql and nothing else"):
            executor.run(not_validated)
    assert spy.streamed == []


@pytest.mark.parametrize(
    "guard",
    [("0", "on", False), ("0ms", "on", False), ("30s", "off", False), ("30s", "on", True)],
)
def test_a_role_with_no_timeout_or_that_may_write_or_a_superuser_is_refused_unrun(guard) -> None:
    spy = _Spy(guard=guard)
    execution = QueryExecutor("postgresql://x/wh", connect=spy).run(valid("SELECT s_store_name FROM store"))
    assert execution.failure == "role_refused" and "nothing was run" in execution.message
    assert spy.streamed == [] and spy.closed


def test_a_cancelled_statement_is_a_timeout_and_any_other_error_a_database_error() -> None:
    cancelled = QueryExecutor("postgresql://x/wh", connect=_Spy(fail=psycopg.errors.QueryCanceled("slow"))).run(
        valid("SELECT s_store_name FROM store")
    )
    assert cancelled.failure == "timeout" and not cancelled.ok
    other = QueryExecutor("postgresql://x/wh", connect=_Spy(fail=psycopg.errors.DivisionByZero("x"))).run(
        valid("SELECT s_store_name FROM store")
    )
    assert other.failure == "database_error" and other.rows == ()


def test_a_warehouse_that_cannot_be_reached_is_an_outcome() -> None:
    def refuse(url, **options):
        raise psycopg.OperationalError("connection refused to host with password=hunter2")

    execution = QueryExecutor("postgresql://x/wh", connect=refuse).run(valid("SELECT s_store_name FROM store"))
    assert execution.failure == "not_reachable" and execution.message == "the warehouse could not be reached"


def test_the_row_cap_must_be_positive() -> None:
    with pytest.raises(ValueError):
        QueryExecutor("postgresql://x/wh", row_cap=0)


# --------------------------------------------------------------------------
# The live warehouse
# --------------------------------------------------------------------------


def test_a_query_returns_its_columns_and_rows(warehouse_dsn) -> None:
    execution = QueryExecutor(warehouse_dsn).run(
        valid("SELECT s.s_store_sk AS store, s.s_state FROM store AS s ORDER BY s.s_store_sk")
    )
    assert execution.ok and execution.columns == ("store", "s_state")
    assert execution.row_count == 12 and not execution.truncated
    assert execution.rows[0][0] == 1 and execution.statement_timeout == "30s"
    assert execution.row_cap == DEFAULT_ROW_CAP


def test_a_percent_sign_in_the_sql_reaches_postgres_as_written(warehouse_dsn) -> None:
    execution = QueryExecutor(warehouse_dsn).run(valid("SELECT COUNT(*) FROM store WHERE s_store_name LIKE '%a%'"))
    assert execution.ok and execution.row_count == 1


def test_at_the_cap_exactly_nothing_is_truncated_and_one_over_is(warehouse_dsn) -> None:
    sql = valid("SELECT s_store_sk FROM store")
    assert QueryExecutor(warehouse_dsn, row_cap=12).run(sql).truncated is False
    over = QueryExecutor(warehouse_dsn, row_cap=11).run(sql)
    assert over.truncated is True and over.row_count == 11


def test_stopping_the_stream_early_stops_the_server_sending(warehouse_dsn) -> None:
    """An unaggregated read of store_sales: 2.9 million rows. Read to the
    end it takes many seconds. Stopped after the cap it must come back at
    once, and the statement must be gone from the server."""
    began = time.monotonic()
    execution = QueryExecutor(warehouse_dsn, row_cap=100).run(valid("SELECT * FROM store_sales"))
    elapsed = time.monotonic() - began

    assert execution.ok and execution.truncated and execution.row_count == 100
    assert elapsed < 5, f"took {elapsed:.1f}s"
    with psycopg.connect(warehouse_dsn) as connection:
        still = connection.execute(
            "select count(*) from pg_stat_activity where state = 'active' and query = 'SELECT * FROM store_sales'"
        ).fetchone()[0]
    assert still == 0


def test_an_error_in_the_database_is_reported_and_nothing_crashes(warehouse_dsn) -> None:
    execution = QueryExecutor(warehouse_dsn).run(valid("SELECT s_store_sk / (s_store_sk - s_store_sk) FROM store"))
    assert execution.failure == "database_error" and execution.sqlstate == "22012"
    assert "division by zero" in execution.message and execution.rows == ()


def test_the_database_itself_refuses_a_write_if_validation_is_taken_away(warehouse_dsn) -> None:
    """NFR-07, the second line of defence, shown by refusal."""
    for sql in ("DELETE FROM store", "CREATE TABLE smuggled (i int)", "UPDATE store SET s_state = 'XX'"):
        execution = QueryExecutor(warehouse_dsn).run(forged(sql))
        assert execution.failure == "database_error", sql
        assert execution.sqlstate in ("25006", "42501"), (sql, execution.sqlstate)
    with psycopg.connect(warehouse_dsn) as connection:
        assert connection.execute("select count(*) from store").fetchone()[0] == 12


def test_the_database_itself_refuses_two_statements_in_one_text(warehouse_dsn) -> None:
    execution = QueryExecutor(warehouse_dsn).run(forged("SELECT 1; SELECT 2"))
    assert execution.failure == "database_error" and execution.sqlstate == "42601"
