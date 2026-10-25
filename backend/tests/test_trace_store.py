"""The trace store: one query row and one trace row, in one transaction,
written once (DD-02 rule 1, DD-07, FR-27, NFR-13, DR-08, DR-09).

Against a scratch database migrated to head. The documents are assembled
from the orchestrator with a scripted model.
"""

import datetime
import json
import uuid
from decimal import Decimal

import psycopg
import pytest
from tracing import document, reply

from app.shell.trace_store import HISTORY_LIMIT, LOCAL_USER_EMAIL, TraceStore

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
        Decimal("0.000254"), 4321, 3,
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
        connection.execute("update traces set trace_version = 4")
    with pytest.raises(ValueError, match="reads trace_version 1 and 2 and 3 and the stored trace is trace_version 4"):
        store.load(written.query_id)


def test_a_trace_written_at_version_1_is_still_read(scratch_database) -> None:
    """DD-07: version 1 is version 2 without `plain` on its warnings. Such
    a trace is read as it was written, and is not rewritten (DD-06)."""
    store = TraceStore(scratch_database)
    written = made(user=store.local_user_id())
    store.save(written)
    assert written.paths.warnings and all("plain" in w for w in written.model_dump(mode="json")["paths"]["warnings"])
    with psycopg.connect(scratch_database) as connection:
        connection.execute(
            """
            update traces set trace_version = 1, body = jsonb_set(
                jsonb_set(body, '{trace_version}', '1'), '{paths,warnings}',
                (select jsonb_agg(w - 'plain') from jsonb_array_elements(body->'paths'->'warnings') w))
            """
        )
    old = store.load(written.query_id)
    assert old.trace_version == 1 and [w.plain for w in old.paths.warnings] == [None] * len(written.paths.warnings)
    assert [w.text for w in old.paths.warnings] == [w.text for w in written.paths.warnings]
    assert old.narrative == written.narrative


# --------------------------------------------------------------------------
# Whose it is, and the history list (step 11)
# --------------------------------------------------------------------------


def _user(url: str, email: str) -> int:
    with psycopg.connect(url) as connection:
        return connection.execute(
            "insert into users (email, password_hash) values (%s, '$argon2id$x') returning id", (email,)
        ).fetchone()[0]


def test_a_query_read_for_its_owner_is_there_and_for_anyone_else_is_not(scratch_database) -> None:
    store = TraceStore(scratch_database)
    ada, bob = _user(scratch_database, "ada@example.com"), _user(scratch_database, "bob@example.com")
    written = made(user=ada)
    store.save(written)
    assert store.load(written.query_id, owner=ada) == written
    assert store.load(written.query_id, owner=bob) is None
    assert store.load(written.query_id) == written, "with no owner named, as the commands read"


def test_history_is_the_owners_newest_first_with_the_columns_a_list_needs(scratch_database) -> None:
    store = TraceStore(scratch_database)
    ada, bob = _user(scratch_database, "ada@example.com"), _user(scratch_database, "bob@example.com")
    start = datetime.datetime(2026, 10, 25, 9, 0, tzinfo=datetime.UTC)
    for n, (owner, script) in enumerate([(ada, None), (bob, None), (ada, [reply(status="not_answerable")]), (ada, None)]):
        written = made(script, user=owner).model_copy(
            update={"question": f"Q{n}?", "created_at": start + datetime.timedelta(minutes=n), "had_ambiguity": n == 3}
        )
        store.save(written)
    listed = store.history(ada)
    assert [row.question for row in listed] == ["Q3?", "Q2?", "Q0?"]
    assert [row.outcome for row in listed] == ["answered", "not_answerable", "answered"]
    assert [row.had_ambiguity for row in listed] == [True, False, False]
    assert listed[0].created_at == start + datetime.timedelta(minutes=3)
    assert [row.question for row in store.history(bob)] == ["Q1?"]
    assert store.history(_user(scratch_database, "cy@example.com")) == []


def test_history_lists_at_most_100(scratch_database) -> None:
    store = TraceStore(scratch_database)
    ada = _user(scratch_database, "ada@example.com")
    start = datetime.datetime(2026, 10, 1, tzinfo=datetime.UTC)
    base = made(user=ada)
    for n in range(103):
        store.save(base.model_copy(update={"query_id": uuid.uuid4(), "question": f"Q{n}?",
                                           "created_at": start + datetime.timedelta(minutes=n)}))  # fmt: skip
    listed = store.history(ada)
    assert len(listed) == HISTORY_LIMIT == 100
    assert (listed[0].question, listed[-1].question) == ("Q102?", "Q3?")
