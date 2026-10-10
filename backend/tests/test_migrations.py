"""The migrations, run for real against a scratch database (DR-14, Design
section 06, DD-07, DD-17).

Every table the application store has was created by a migration and by
nothing else. These tests run them up and down, and then ask Postgres to
refuse the things the schema exists to refuse. The first migration (step
6) made the three schema tables; the second (step 8) makes users, queries
and traces, with user_id on queries from the moment the table exists
(DR-08).
"""

import json
import uuid


import psycopg
import pytest
from alembic import command
from appdb import alembic_config

SCHEMA_TABLES = ["schema_edges", "schema_elements", "schema_snapshots"]
TABLES = ["queries", "schema_edges", "schema_elements", "schema_snapshots", "traces", "users"]
LOCAL_USER = "local@cartograph.invalid"


def _tables(url: str) -> list[str]:
    with psycopg.connect(url) as connection:
        rows = connection.execute(
            "select tablename from pg_tables where schemaname = 'public' and tablename <> 'alembic_version' order by 1"
        ).fetchall()
    return [name for (name,) in rows]


def _snapshot(connection, hash_: str = "h1", current: bool = False) -> int:
    return connection.execute(
        "insert into schema_snapshots (hash, source, is_current) values (%s, 'test', %s) returning id",
        (hash_, current),
    ).fetchone()[0]


def _element(connection, snapshot: int, table: str, column: str | None, text: str = "x", position: int = 0) -> int:
    return connection.execute(
        """
        insert into schema_elements
            (snapshot_id, table_name, column_name, position, data_type, readable, description, search_text)
        values (%s, %s, %s, %s, %s, %s, %s, %s) returning id
        """,
        (snapshot, table, column, position, "bigint" if column else None, text, text, text),
    ).fetchone()[0]


def test_the_first_migration_creates_exactly_the_three_schema_tables(empty_database) -> None:
    assert _tables(empty_database) == []
    command.upgrade(alembic_config(empty_database), "0001")
    assert _tables(empty_database) == SCHEMA_TABLES


def test_upgrade_to_head_creates_exactly_the_six_tables(empty_database) -> None:
    command.upgrade(alembic_config(empty_database), "head")
    assert _tables(empty_database) == TABLES


def test_the_second_migration_comes_down_alone_and_leaves_the_schema_tables(scratch_database) -> None:
    config = alembic_config(scratch_database)
    command.downgrade(config, "0001")
    assert _tables(scratch_database) == SCHEMA_TABLES
    command.upgrade(config, "head")
    assert _tables(scratch_database) == TABLES


def test_downgrade_removes_them_and_upgrade_brings_them_back(scratch_database) -> None:
    config = alembic_config(scratch_database)
    command.downgrade(config, "base")
    assert _tables(scratch_database) == []
    command.upgrade(config, "head")
    assert _tables(scratch_database) == TABLES


# --------------------------------------------------------------------------
# users, queries, traces (step 8)
# --------------------------------------------------------------------------


def _local_user(connection) -> int:
    return connection.execute("select id from users where email = %s", (LOCAL_USER,)).fetchone()[0]


def _query(connection, user: int | None, outcome: str = "answered", conformance: str | None = "conforms") -> uuid.UUID:
    query_id = uuid.uuid4()
    connection.execute(
        "insert into queries (id, user_id, question, created_at, outcome, had_ambiguity, conformance_result, "
        "cost_estimate, duration_ms) values (%s, %s, 'q', now(), %s, false, %s, 0.000254, 12)",
        (query_id, user, outcome, conformance),
    )
    return query_id


def test_the_migration_seeds_one_local_user_who_cannot_log_in(scratch_database) -> None:
    """Until step 11 every query belongs to this user. A null hash is a
    user nobody can log in as."""
    with psycopg.connect(scratch_database) as connection:
        assert connection.execute("select email, password_hash from users").fetchall() == [(LOCAL_USER, None)]


def test_an_email_is_held_once(scratch_database) -> None:
    with psycopg.connect(scratch_database) as connection:
        with pytest.raises(psycopg.errors.UniqueViolation):
            connection.execute("insert into users (email) values (%s)", (LOCAL_USER,))


def test_a_query_belongs_to_a_user_who_exists(scratch_database) -> None:
    """DR-08: user identity from the moment the table exists."""
    with psycopg.connect(scratch_database) as connection:
        _query(connection, _local_user(connection))
        connection.commit()
        with pytest.raises(psycopg.errors.NotNullViolation):
            _query(connection, None)
        connection.rollback()
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            _query(connection, 999999)


def test_an_outcome_and_a_conformance_result_are_ones_the_design_names(scratch_database) -> None:
    with psycopg.connect(scratch_database) as connection:
        user = _local_user(connection)
        for outcome in ("answered", "not_answerable", "validation_failed", "model_failed", "execution_failed"):
            _query(connection, user, outcome, None)
        for conformance in ("conforms", "diverged", "incomplete", "not_checked"):
            _query(connection, user, "answered", conformance)
        connection.commit()
        with pytest.raises(psycopg.errors.CheckViolation):
            _query(connection, user, "guessed")
        connection.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):
            _query(connection, user, "answered", "probably")


def test_a_trace_is_one_per_query_and_needs_its_query(scratch_database) -> None:
    with psycopg.connect(scratch_database) as connection:
        query_id = _query(connection, _local_user(connection))
        body = json.dumps({"trace_version": 1})
        connection.execute("insert into traces (query_id, trace_version, body) values (%s, 1, %s)", (query_id, body))
        connection.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            connection.execute("insert into traces (query_id, trace_version, body) values (%s, 1, %s)", (query_id, body))
        connection.rollback()
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            connection.execute("insert into traces (query_id, trace_version, body) values (%s, 1, %s)", (uuid.uuid4(), body))


def test_the_five_extracted_columns_are_on_queries_and_the_body_is_jsonb(scratch_database) -> None:
    """DD-07 and figure 4: a history list never reads the traces table."""
    with psycopg.connect(scratch_database) as connection:
        columns = dict(
            connection.execute(
                "select table_name || '.' || column_name, data_type from information_schema.columns "
                "where table_name in ('queries', 'traces')"
            ).fetchall()
        )
    for name in ("outcome", "had_ambiguity", "conformance_result", "cost_estimate", "duration_ms", "user_id"):
        assert f"queries.{name}" in columns
    assert columns["traces.body"] == "jsonb" and columns["queries.id"] == "uuid"
    assert set(name for name in columns if name.startswith("traces.")) == {"traces.query_id", "traces.trace_version", "traces.body"}


def test_at_most_one_snapshot_is_current(scratch_database) -> None:
    with psycopg.connect(scratch_database) as connection:
        _snapshot(connection, "h1", current=True)
        _snapshot(connection, "h2", current=False)
        with pytest.raises(psycopg.errors.UniqueViolation):
            _snapshot(connection, "h3", current=True)


def test_the_same_schema_is_not_stored_twice(scratch_database) -> None:
    with psycopg.connect(scratch_database) as connection:
        _snapshot(connection, "h1")
        with pytest.raises(psycopg.errors.UniqueViolation):
            _snapshot(connection, "h1")


def test_the_search_vector_is_generated_from_the_text_and_stems_it(scratch_database) -> None:
    with psycopg.connect(scratch_database) as connection:
        snapshot = _snapshot(connection)
        _element(connection, snapshot, "sales", "bill_addr", "catalog sales — bill address surrogate key")
        _element(connection, snapshot, "sales", "ship_addr", "catalog sales — ship address surrogate key", 1)

        found = connection.execute(
            "select column_name from schema_elements where fts @@ to_tsquery('english', 'billed')"
        ).fetchall()
        assert found == [("bill_addr",)]

        # Generated: it cannot be written, so it cannot drift from the text.
        with pytest.raises(psycopg.errors.GeneratedAlways):
            connection.execute("update schema_elements set fts = to_tsvector('english', 'other')")


def test_an_embedding_has_1536_dimensions_or_is_refused(scratch_database) -> None:
    with psycopg.connect(scratch_database) as connection:
        snapshot = _snapshot(connection)
        element = _element(connection, snapshot, "sales", None)
        vector = "[" + ",".join(["0.5"] * 1536) + "]"
        connection.execute("update schema_elements set embedding = %s::vector where id = %s", (vector, element))
        connection.commit()
        with pytest.raises(psycopg.errors.DataException):
            connection.execute("update schema_elements set embedding = '[1,2,3]'::vector where id = %s", (element,))


def test_an_element_is_stored_once_per_snapshot_table_rows_included(scratch_database) -> None:
    with psycopg.connect(scratch_database) as connection:
        snapshot = _snapshot(connection)
        _element(connection, snapshot, "sales", None)
        _element(connection, snapshot, "sales", "amount", position=1)
        connection.commit()
        for column in (None, "amount"):
            with pytest.raises(psycopg.errors.UniqueViolation):
                _element(connection, snapshot, "sales", column, position=2)
            connection.rollback()


def test_a_column_has_a_type_and_a_table_row_has_none(scratch_database) -> None:
    with psycopg.connect(scratch_database) as connection:
        snapshot = _snapshot(connection)
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "insert into schema_elements (snapshot_id, table_name, column_name, position, readable, "
                "description, search_text) values (%s, 'sales', 'amount', 0, 'x', 'x', 'x')",
                (snapshot,),
            )


def test_an_edge_is_one_column_pair_and_a_key_of_two_columns_is_two_rows(scratch_database) -> None:
    with psycopg.connect(scratch_database) as connection:
        snapshot = _snapshot(connection)
        ids = [_element(connection, snapshot, "t", name, position=n) for n, name in enumerate("abcd")]

        def edge(key: int, position: int, start: int, end: int, source: str = "overlay") -> None:
            connection.execute(
                "insert into schema_edges (snapshot_id, key_number, position, from_element_id, to_element_id, "
                "source, note) values (%s, %s, %s, %s, %s, %s, 'why')",
                (snapshot, key, position, start, end, source),
            )

        edge(0, 0, ids[0], ids[2])
        edge(0, 1, ids[1], ids[3])
        connection.commit()
        assert connection.execute("select count(*), count(distinct note) from schema_edges").fetchone() == (2, 1)

        with pytest.raises(psycopg.errors.UniqueViolation):
            edge(0, 1, ids[0], ids[3])
        connection.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):
            edge(1, 0, ids[0], ids[2], source="guessed")
        connection.rollback()
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            edge(2, 0, ids[0], 999999)
