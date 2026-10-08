"""The first migration, run for real against a scratch database (DR-14,
Design section 06, DD-17).

Every table the application store has was created by this migration and by
nothing else. These tests run it up and down, and then ask Postgres to
refuse the things the schema exists to refuse.
"""

import psycopg
import pytest
from alembic import command
from appdb import alembic_config

TABLES = ["schema_edges", "schema_elements", "schema_snapshots"]


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


def test_upgrade_creates_exactly_the_three_schema_tables(empty_database) -> None:
    assert _tables(empty_database) == []
    command.upgrade(alembic_config(empty_database), "head")
    assert _tables(empty_database) == TABLES


def test_downgrade_removes_them_and_upgrade_brings_them_back(scratch_database) -> None:
    config = alembic_config(scratch_database)
    command.downgrade(config, "base")
    assert _tables(scratch_database) == []
    command.upgrade(config, "head")
    assert _tables(scratch_database) == TABLES


def test_no_users_queries_or_traces_yet(scratch_database) -> None:
    """They arrive at step 8, with user_id from the first moment (DR-08)."""
    assert not {"users", "queries", "traces"} & set(_tables(scratch_database))


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
