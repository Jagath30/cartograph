"""The trace store: one query row and one trace row, in one transaction,
written once (DD-02 rule 1, DD-07, FR-27, NFR-13, DR-08, DR-09).

Against a scratch database migrated to head. The documents are assembled
from the orchestrator with a scripted model.
"""

import json
import uuid
from decimal import Decimal

import psycopg
import pytest
from tracing import document, reply

from app.shell.trace_store import LOCAL_USER_EMAIL, TraceStore

ADDRESS = ("catalog_sales", "customer_address")
BILL = "SELECT ca.ca_state FROM catalog_sales cs JOIN customer_address ca ON cs.cs_bill_addr_sk = ca.ca_address_sk"


def made(script=None, user: int = 1, query_id: uuid.UUID | None = None):
    found = document(script or [reply(BILL)], *ADDRESS)
    return found.model_copy(update={"user_id": user, "query_id": query_id or uuid.uuid4()})


def counts(url: str) -> tuple[int, int]:
    with psycopg.connect(url) as connection:
        return connection.execute("select (select count(*) from queries), (select count(*) from traces)").fetchone()


def test_the_local_user_is_the_one_the_migration_seeded(scratch_database) -> None:
    store = TraceStore(scratch_database)
    with psycopg.connect(scratch_database) as connection:
        (expected,) = connection.execute("select id from users where email = %s", (LOCAL_USER_EMAIL,)).fetchone()
    assert store.local_user_id() == expected


def test_a_trace_is_stored_with_its_query_and_read_back_identical(scratch_database) -> None:
    store = TraceStore(scratch_database)
    written = made(user=store.local_user_id())
    store.save(written)
    assert counts(scratch_database) == (1, 1)
    read = store.load(written.query_id)
    assert read == written
    assert json.loads(read.model_dump_json()) == json.loads(written.model_dump_json())


def test_the_five_extracted_columns_are_lifted_at_write_time(scratch_database) -> None:
    """DD-07: outcome, had_ambiguity, conformance_result, cost_estimate,
    duration_ms -- and the question, the user and the time (DR-08)."""
    store = TraceStore(scratch_database)
    written = made(user=store.local_user_id()).model_copy(update={"cost_usd": 0.000254, "duration_ms": 4321})
    store.save(written)
    with psycopg.connect(scratch_database) as connection:
        row = connection.execute(
            "select q.user_id, q.question, q.created_at, q.outcome, q.had_ambiguity, q.conformance_result, "
            "q.cost_estimate, q.duration_ms, t.trace_version from queries q join traces t on t.query_id = q.id "
            "where q.id = %s",
            (written.query_id,),
        ).fetchone()
    assert row == (
        written.user_id, written.question, written.created_at, "answered", True, "conforms",
        Decimal("0.000254"), 4321, 1,
    )  # fmt: skip


def test_a_decline_is_stored_with_no_conformance_result(scratch_database) -> None:
    store = TraceStore(scratch_database)
    written = made([reply(status="not_answerable")], user=store.local_user_id())
    store.save(written)
    with psycopg.connect(scratch_database) as connection:
        row = connection.execute("select outcome, had_ambiguity, conformance_result from queries").fetchone()
    assert row == ("not_answerable", False, None)
    assert store.load(written.query_id).generation.tables_shown == ["catalog_sales", "customer_address"]


def test_the_two_rows_are_written_together_or_not_at_all(scratch_database) -> None:
    """NFR-13, DD-02: there is no partial write to lose. Here the trace
    row is refused, and the query row must not survive it."""
    store = TraceStore(scratch_database)
    with psycopg.connect(scratch_database) as connection:
        connection.execute("alter table traces add constraint refuse_all check (trace_version < 0)")
    with pytest.raises(psycopg.Error):
        store.save(made(user=store.local_user_id()))
    assert counts(scratch_database) == (0, 0)


def test_a_query_for_a_user_who_does_not_exist_writes_nothing(scratch_database) -> None:
    store = TraceStore(scratch_database)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        store.save(made(user=999999))
    assert counts(scratch_database) == (0, 0)


def test_a_trace_is_written_once_and_a_second_write_is_refused(scratch_database) -> None:
    """Written once, never updated (DD-07)."""
    store = TraceStore(scratch_database)
    written = made(user=store.local_user_id())
    store.save(written)
    with pytest.raises(psycopg.errors.UniqueViolation):
        store.save(written.model_copy(update={"question": "another question"}))
    assert counts(scratch_database) == (1, 1)
    assert store.load(written.query_id).question == written.question


def test_an_unknown_query_is_none(scratch_database) -> None:
    assert TraceStore(scratch_database).load(uuid.uuid4()) is None


def test_the_rows_of_the_warehouse_are_not_stored(scratch_database) -> None:
    """DR-15. Everything the store holds for a query is read back as text
    and searched."""
    from dataclasses import replace

    from tracing import Runs

    class Telling(Runs):
        def run(self, validated):
            return replace(super().run(validated), rows=(("ZZ-SENTINEL-ROW",),))

    store = TraceStore(scratch_database)
    written = document([reply(BILL)], *ADDRESS, executor=Telling()).model_copy(update={"user_id": store.local_user_id()})
    store.save(written)
    with psycopg.connect(scratch_database) as connection:
        (held,) = connection.execute(
            "select (select string_agg(q::text, '') from queries q) || (select string_agg(t::text, '') from traces t)"
        ).fetchone()
    assert "catalog_sales" in held and "ZZ-SENTINEL-ROW" not in held


def test_a_body_of_another_version_is_not_read_as_this_one(scratch_database) -> None:
    store = TraceStore(scratch_database)
    written = made(user=store.local_user_id())
    store.save(written)
    with psycopg.connect(scratch_database) as connection:
        connection.execute("update traces set trace_version = 2")
    with pytest.raises(ValueError, match="trace_version 2"):
        store.load(written.query_id)
