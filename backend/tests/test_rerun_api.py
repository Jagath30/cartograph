"""Running a stored answer's SQL again (step 11, item 88; FR-18 to FR-20,
FR-22 to FR-25, NFR-07, NFR-08, NFR-11).

    POST /api/v1/queries/{id}/rerun     the owner's stored SQL, validated
                                        again and run now; rows returned,
                                        nothing stored

A stored answer keeps no rows (DR-15). Reopened, it can be run again: the
stored SQL is validated again exactly as an answer's is, against the
schema as stored now, and run on the read-only role with the same
timeout and row cap. No model is called. The rows are the data as of now,
not as of the question.
"""

import uuid
from datetime import UTC, datetime

import pytest
from api_client import NoSchema, Snapshots, client, service
from memory_stores import Memory
from samples import SMALL
from tracing import Runs, reply

from app.auth import now
from app.config import Settings
from app.core.sql_validator import ValidatedSql
from app.shell import model_client, pipeline_session, query_service
from app.shell.query_executor import DEFAULT_ROW_CAP, QueryExecutor
from app.shell.snapshot_store import SnapshotStore
from app.shell.trace_store import TraceStore

BILL = "SELECT ca.ca_state FROM catalog_sales cs JOIN customer_address ca ON cs.cs_bill_addr_sk = ca.ca_address_sk"
RAN_AT = datetime(2026, 10, 26, 8, 15, tzinfo=UTC)


def stored(script, executor=None, rerun=None, snapshots=None, opened=None):
    """A service that has answered one question, and that answer's id."""
    traces = Memory()
    made = service(script, executor=executor, traces=traces, snapshots=snapshots or Snapshots(), opened=opened,
                   rerun_executor=rerun if rerun is not None else Runs(rows=3))  # fmt: skip
    asked = client(made).post("/api/v1/queries", json={"question": "Where are catalogue buyers billed?"}).json()
    return made, traces, asked


def rerun(made, query_id):
    web = client(made)
    web.app.dependency_overrides[now] = lambda: RAN_AT
    return web.post(f"/api/v1/queries/{query_id}/rerun")


# --------------------------------------------------------------------------
# Run again
# --------------------------------------------------------------------------


def test_a_stored_answer_is_run_again_and_its_rows_are_returned() -> None:
    again = Runs(rows=3)
    made, _, asked = stored([reply(BILL)], rerun=again)
    response = rerun(made, asked["query_id"])
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"query_id", "ran_at", "status", "code", "message", "columns", "rows", "row_count", "truncated"}
    assert (body["query_id"], body["status"], body["code"], body["message"]) == (asked["query_id"], "answered", None, None)
    assert (body["columns"], body["rows"], body["row_count"], body["truncated"]) == (["a"], [[0], [1], [2]], 3, False)
    assert body["ran_at"] == "2026-10-26T08:15:00Z"


def test_the_sql_run_again_is_the_stored_sql_byte_for_byte_and_sealed_by_the_validator() -> None:
    again = Runs()
    made, _, asked = stored([reply(BILL)], rerun=again)
    rerun(made, asked["query_id"])
    (handed,) = again.handed
    assert isinstance(handed, ValidatedSql) and handed.text == asked["trace"]["execution"]["sql"] == BILL


def test_the_rows_are_returned_and_nothing_is_stored() -> None:
    made, traces, asked = stored([reply(BILL)])
    before = dict(traces.bodies)
    assert rerun(made, asked["query_id"]).json()["rows"] == [[0], [1], [2]]
    assert traces.bodies == before, "the stored trace is not changed, and no new one is written"
    read = client(made).get(f"/api/v1/queries/{asked['query_id']}").json()
    assert read["rows"] is None and read["trace"] == asked["trace"]


def test_a_truncated_result_says_so() -> None:
    made, _, asked = stored([reply(BILL)], rerun=Runs(rows=2, truncated=True))
    assert rerun(made, asked["query_id"]).json()["truncated"] is True


def test_a_query_the_database_stops_is_an_execution_failure_with_what_it_said() -> None:
    made, _, asked = stored([reply(BILL)], rerun=Runs(failure="database_error"))
    body = rerun(made, asked["query_id"]).json()
    assert (body["status"], body["code"], body["message"]) == ("execution_failed", "execution_failure", "division by zero")
    assert body["rows"] is None and body["row_count"] is None and body["columns"] == []


# --------------------------------------------------------------------------
# Validated again: no path around validation (NFR-08)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("written", ["DROP TABLE store", "DELETE FROM catalog_sales", "SELECT pg_sleep(60)"])
def test_a_stored_sql_that_would_write_or_wait_is_refused_and_nothing_runs(written) -> None:
    """The brief: a rerun revalidates, and a stored SQL that would write
    is refused. The answer that stored it was refused too; the rerun does
    not trust that, it asks again."""
    again = Runs()
    made, _, asked = stored([reply(written)], rerun=again)
    assert asked["status"] == "validation_failed"
    response = rerun(made, asked["query_id"])
    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["code"]) == ("validation_failed", "validation_failure")
    assert body["message"] and body["rows"] is None and body["columns"] == []
    assert again.handed == [], "nothing was run"


def test_a_stored_sql_is_validated_against_the_schema_as_stored_now() -> None:
    """It passed when it was asked; the schema stored now has none of its
    tables, so it is refused, as a new answer's SQL would be."""
    again = Runs()
    made, _, asked = stored([reply(BILL)], rerun=again)
    assert asked["status"] == "answered"
    made._snapshots = Snapshots(SMALL)
    body = rerun(made, asked["query_id"]).json()
    assert (body["status"], body["code"]) == ("validation_failed", "validation_failure")
    assert "catalog_sales" in body["message"]
    assert again.handed == []


def test_a_stored_sql_that_was_tampered_with_is_validated_as_it_now_reads() -> None:
    """Whatever the store holds is judged, not what was judged before."""
    again = Runs()
    made, traces, asked = stored([reply(BILL)], rerun=again)
    query_id = uuid.UUID(asked["query_id"])
    document = traces.load(query_id)
    tampered = document.model_copy(update={"execution": document.execution.model_copy(update={"sql": "DROP TABLE store"})})
    traces.bodies[query_id] = tampered.model_dump_json()
    assert rerun(made, asked["query_id"]).json()["status"] == "validation_failed"
    assert again.handed == []


def test_an_answer_that_never_ran_is_rerun_from_the_last_sql_written() -> None:
    """A query the database stopped kept its SQL: it is run again."""
    made, _, asked = stored([reply(BILL)], executor=Runs(failure="database_error"))
    assert asked["status"] == "execution_failed"
    assert rerun(made, asked["query_id"]).json()["status"] == "answered"


def test_of_several_sql_written_and_none_run_the_last_is_the_one_rerun() -> None:
    """Asked again after a refusal, the model's last SQL is the answer's;
    the first was refused and replaced."""
    from tracing import document

    written = document([reply("SELECT nope FROM store"), reply("DROP TABLE store")], "store_sales", "store")
    assert written.execution is None
    assert [attempt.sql for attempt in written.generation.attempts] == ["SELECT nope FROM store", "DROP TABLE store"]
    assert query_service.stored_sql(written) == "DROP TABLE store"


@pytest.mark.parametrize(
    "script",
    [[reply(status="not_answerable")],
     [model_client.ModelReply("m", None, None, None, 0, 0, 0, 0.0, 0, "transport", model_client.FAILURES["transport"])]],
)  # fmt: skip
def test_an_answer_with_no_sql_has_nothing_to_rerun(script) -> None:
    again = Runs()
    made, _, asked = stored(script, rerun=again)
    response = rerun(made, asked["query_id"])
    assert response.status_code == 409
    assert response.json() == {"code": "nothing_to_rerun", "message": "This answer has no SQL to run again."}
    assert again.handed == []


# --------------------------------------------------------------------------
# No model, the read-only role, the same timeout and row cap
# --------------------------------------------------------------------------


def test_a_rerun_never_opens_the_pipeline() -> None:
    opened: list = []
    made, _, asked = stored([reply(BILL)], opened=opened)
    assert opened == [1]
    opened.clear()
    fresh = query_service.QueryService(lambda: opened.append(1), made._traces, Snapshots(), Runs())
    assert rerun(fresh, asked["query_id"]).json()["status"] == "answered"
    assert opened == []


def test_a_rerun_through_the_real_service_constructs_no_model_client(monkeypatch) -> None:
    """The owner's ruling 6. Every way a model client is made is made to
    fail; a rerun through make_service still answers, so none was made."""

    def no_model(*arguments, **keywords):
        raise AssertionError("a model client was constructed during a rerun")

    monkeypatch.setattr(model_client.OpenAIModel, "__init__", no_model)
    monkeypatch.setattr(model_client, "make_model", no_model)
    monkeypatch.setattr(pipeline_session, "make_model", no_model)
    monkeypatch.setattr(pipeline_session, "open_pipeline", no_model)

    _, held, asked = stored([reply(BILL)])
    monkeypatch.setattr(TraceStore, "load", lambda self, query_id, owner=None: held.load(query_id, owner))
    monkeypatch.setattr(SnapshotStore, "load_current", lambda self: Snapshots().load_current())
    handed: list = []
    monkeypatch.setattr(QueryExecutor, "run", lambda self, validated: handed.append(validated) or Runs().run(validated))

    real = query_service.make_service("postgresql://nowhere/app", "postgresql://nowhere/warehouse")
    response = rerun(real, asked["query_id"])
    assert response.status_code == 200 and response.json()["status"] == "answered"
    assert [validated.text for validated in handed] == [BILL]


def test_the_real_rerun_runs_on_the_warehouse_role_with_the_answers_row_cap() -> None:
    """The same executor an answer runs on: the warehouse's connection
    string, whose role is read-only and carries statement_timeout (the
    executor refuses any other), and the same row cap."""
    real = query_service.make_service("postgresql://nowhere/app", "postgresql://ro@nowhere/warehouse")
    executor = real._rerun
    assert isinstance(executor, QueryExecutor)
    assert (executor._url, executor._cap) == ("postgresql://ro@nowhere/warehouse", DEFAULT_ROW_CAP)


def test_the_api_builds_the_real_service_with_both_databases(monkeypatch) -> None:
    from app import api

    built: list = []
    monkeypatch.setattr(api, "make_service", lambda *urls: built.append(urls) or "service")
    monkeypatch.setattr(api, "get_settings", lambda: Settings(app_database_url="a", warehouse_database_url="w", redis_url="r"))
    api.get_service.cache_clear()
    try:
        assert api.get_service() == "service" and built == [("a", "w")]
    finally:
        api.get_service.cache_clear()


def test_a_rerun_with_no_schema_stored_is_a_503() -> None:
    made, _, asked = stored([reply(BILL)])
    made._snapshots = NoSchema()
    response = rerun(made, asked["query_id"])
    assert response.status_code == 503 and response.json()["code"] == "not_ready"


def test_every_new_response_body_is_declared() -> None:
    """IR-11, for the routes of step 11: each success and each refusal
    names its schema."""
    from api_client import SETTINGS

    from app.main import create_app

    described = create_app(SETTINGS).openapi()["paths"]
    routes = [("/api/v1/auth/register", "post", "201"), ("/api/v1/auth/login", "post", "200"), ("/api/v1/auth/me", "get", "200"),
              ("/api/v1/queries", "get", "200"), ("/api/v1/queries/{query_id}/rerun", "post", "200")]  # fmt: skip
    for path, method, success in routes:
        responses = described[path][method]["responses"]
        for status in (success, "401", "403", "415", "422", "500", "503"):
            assert "$ref" in responses[status]["content"]["application/json"]["schema"], (path, status)
    assert "409" in described["/api/v1/queries/{query_id}/rerun"]["post"]["responses"]
    assert "409" in described["/api/v1/auth/register"]["post"]["responses"]
