"""The API of step 8 (IR-01 to IR-06, IR-11, FR-26, FR-27, NFR-06, NFR-13).

    POST /api/v1/queries          a question in; id, status, rows, trace out
    GET  /api/v1/queries/{id}     one stored query with its trace
    GET  /api/v1/schema           the stored schema as nodes and edges

The pipeline behind it is the real orchestrator with a scripted model, a
stand-in executor and no network. Most tests keep traces in memory; those
that take `scratch_database` use the real stores on a scratch database.
"""

import datetime
import json
import threading
import time
import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from samples import SMALL
from tracing import CONTEXT, SCHEMA_REF, SNAPSHOT, Runs, located, orchestrator, reply

from app.api import get_service
from app.config import Settings
from app.core.trace_document import TraceDocument
from app.main import create_app
from app.orchestrator import Keep, Orchestrator
from app.shell.model_client import FAILURES, ModelKeyMissing, ModelReply
from app.shell.query_executor import Execution
from app.shell.query_service import QueryService
from app.shell.snapshot_store import NoCurrentSnapshot, SnapshotStore
from app.shell.trace_store import TraceStore

ADDRESS = ("catalog_sales", "customer_address")
BILL = "SELECT ca.ca_state FROM catalog_sales cs JOIN customer_address ca ON cs.cs_bill_addr_sk = ca.ca_address_sk"
STORE_SQL = "SELECT s.s_store_name FROM store_sales ss JOIN store s ON ss.ss_store_sk = s.s_store_sk"
SETTINGS = Settings(
    app_database_url="postgresql://unused/unused", warehouse_database_url="postgresql://unused/unused", redis_url="redis://unused"
)


class Memory:
    """A trace store that keeps what it was given as the JSON it would
    have written, so that reading back goes through the same door."""

    def __init__(self, refuse: Exception | None = None) -> None:
        self.bodies: dict[uuid.UUID, str] = {}
        self.refuse = refuse

    def save(self, document: TraceDocument) -> None:
        if self.refuse:
            raise self.refuse
        self.bodies[document.query_id] = document.model_dump_json()

    def load(self, query_id: uuid.UUID) -> TraceDocument | None:
        body = self.bodies.get(query_id)
        return TraceDocument.model_validate_json(body) if body else None


class NoSchema:
    def load_current(self):
        raise NoCurrentSnapshot("the application store holds no schema snapshot: nothing has been ingested")


def service(script, anchors=ADDRESS, executor=None, traces=None, snapshots=None, opened=None, **tree_arguments) -> QueryService:
    traces = traces if traces is not None else Memory()

    def open_orchestrator() -> Orchestrator:
        if opened is not None:
            opened.append(1)
        keep = Keep(traces, CONTEXT, user_id=1)
        return orchestrator(script, located(*anchors, **tree_arguments), executor, keep=keep)

    return QueryService(open_orchestrator, traces, snapshots or NoSchema())


def client(made: QueryService) -> TestClient:
    app = create_app(SETTINGS)
    app.dependency_overrides[get_service] = lambda: made
    return TestClient(app, raise_server_exceptions=False)


def ask(made: QueryService, question: str = "Where are catalogue buyers billed?"):
    return client(made).post("/api/v1/queries", json={"question": question})


# --------------------------------------------------------------------------
# A question answered
# --------------------------------------------------------------------------


def test_a_question_returns_its_id_its_status_its_rows_and_its_trace() -> None:
    """IR-02, IR-03."""
    response = ask(service([reply(BILL)]))
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"query_id", "status", "code", "message", "columns", "rows", "row_count", "truncated", "trace"}
    assert uuid.UUID(body["query_id"]).version == 4
    assert (body["status"], body["code"], body["message"]) == ("answered", None, None)
    assert (body["columns"], body["rows"], body["row_count"], body["truncated"]) == (["a"], [[0], [1]], 2, False)
    trace = body["trace"]
    assert trace["query_id"] == body["query_id"] and trace["trace_version"] == 1
    assert trace["question"] == "Where are catalogue buyers billed?"
    assert trace["execution"]["sql"] == BILL and trace["validation"]["conformance"] == "conforms"
    assert set(trace["narrative"]) == {"found", "route", "not_taken", "sql", "result"}
    assert trace["narrative"]["result"] == "2 rows came back."


def test_the_trace_holds_the_row_count_and_the_response_holds_the_rows() -> None:
    """DR-15: returned, not stored."""
    body = ask(service([reply(BILL)])).json()
    assert body["rows"] == [[0], [1]] and body["trace"]["execution"]["row_count"] == 2
    assert "rows" not in body["trace"]["execution"]


def test_the_question_is_trimmed_before_it_is_asked() -> None:
    body = ask(service([reply(BILL)]), "   Where are buyers billed?  \n").json()
    assert body["trace"]["question"] == "Where are buyers billed?"


def test_values_json_cannot_carry_are_given_as_text() -> None:
    class Mixed(Runs):
        def run(self, validated):
            return Execution(
                executed=validated.text, columns=("n", "d", "t", "s", "b", "nothing"),
                rows=((Decimal("1234567.89"), datetime.date(2001, 3, 4), datetime.datetime(2001, 3, 4, 5, 6, 7), "x", True, None),),
            )  # fmt: skip

    body = ask(service([reply(BILL)], executor=Mixed())).json()
    assert body["rows"] == [["1234567.89", "2001-03-04", "2001-03-04T05:06:07", "x", True, None]]


# --------------------------------------------------------------------------
# Stored, and read back
# --------------------------------------------------------------------------


def test_the_stored_trace_read_back_is_identical_to_the_one_returned() -> None:
    made = service([reply(BILL)])
    asked = ask(made).json()
    read = client(made).get(f"/api/v1/queries/{asked['query_id']}")
    assert read.status_code == 200
    assert read.json()["trace"] == asked["trace"]


def test_a_stored_query_has_its_status_and_columns_and_no_rows() -> None:
    made = service([reply(BILL)])
    asked = ask(made).json()
    read = client(made).get(f"/api/v1/queries/{asked['query_id']}").json()
    assert (read["query_id"], read["status"], read["columns"], read["row_count"]) == (asked["query_id"], "answered", ["a"], 2)
    assert read["rows"] is None


def test_through_the_real_stores_the_trace_read_back_is_identical(scratch_database) -> None:
    """FR-27, NFR-13: one question through POST, its stored trace read
    back through GET, on a real database."""
    traces = TraceStore(scratch_database)
    traces_user = traces.local_user_id()

    def open_orchestrator() -> Orchestrator:
        return orchestrator([reply(BILL)], located(*ADDRESS), keep=Keep(traces, CONTEXT, traces_user))

    made = QueryService(open_orchestrator, traces, SnapshotStore(scratch_database))
    asked = ask(made).json()
    read = client(made).get(f"/api/v1/queries/{asked['query_id']}").json()
    assert read["trace"] == asked["trace"] and read["trace"]["user_id"] == traces_user
    assert json.dumps(read["trace"], sort_keys=True) == json.dumps(asked["trace"], sort_keys=True)


def test_an_unknown_query_is_a_404_with_a_code_and_a_message() -> None:
    response = client(service([])).get(f"/api/v1/queries/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found" and "is stored" in response.json()["message"]


def test_an_id_that_is_not_an_id_is_a_malformed_request() -> None:
    response = client(service([])).get("/api/v1/queries/seven")
    assert response.status_code == 422 and response.json()["code"] == "invalid_request"


# --------------------------------------------------------------------------
# Every outcome of the pipeline is a result (the ruling at stop 1)
# --------------------------------------------------------------------------


def test_a_decline_is_a_200_with_its_trace_and_the_code_and_message_of_ir05() -> None:
    made = service([reply(status="not_answerable")])
    response = ask(made)
    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["code"]) == ("not_answerable", "question_not_answerable")
    assert body["message"].startswith("This cannot be answered from the tables retrieved")
    assert (body["columns"], body["rows"], body["row_count"]) == ([], None, None)
    assert body["trace"]["generation"]["tables_shown"] == ["catalog_sales", "customer_address"]
    assert "catalog sales and customer address" in body["trace"]["narrative"]["result"]
    assert client(made).get(f"/api/v1/queries/{body['query_id']}").json()["trace"] == body["trace"]


@pytest.mark.parametrize(
    ("script", "executor", "status", "code"),
    [
        ([reply(BILL)], Runs(failure="database_error"), "execution_failed", "execution_failure"),
        ([reply("DROP TABLE store")], None, "validation_failed", "validation_failure"),
        ([ModelReply("m", None, None, None, 0, 0, 0, 0.0, 0, "transport", FAILURES["transport"])], None, "model_failed", "model_failure"),
    ],
)
def test_a_failure_of_the_pipeline_is_a_200_with_its_trace_and_is_stored(script, executor, status, code) -> None:
    """DD-04: a failure is as legible as a success. It is not a 5xx: the
    server did what it is for, and the trace says what happened."""
    made = service(script, executor=executor)
    response = ask(made)
    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["code"]) == (status, code) and body["message"]
    assert body["rows"] is None and body["row_count"] is None and body["columns"] == []
    assert body["trace"]["outcome"] == status
    assert client(made).get(f"/api/v1/queries/{body['query_id']}").json()["trace"] == body["trace"]


def test_a_query_the_database_stopped_keeps_what_the_database_said() -> None:
    body = ask(service([reply(BILL)], executor=Runs(failure="database_error"))).json()
    assert body["message"] == "division by zero" and body["trace"]["execution"]["sqlstate"] == "22012"
    assert body["trace"]["narrative"]["result"].startswith("The query was run and the database stopped it")


# --------------------------------------------------------------------------
# 4xx: a malformed request, and nothing else
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sent",
    [
        {"question": ""},
        {"question": "    "},
        {},
        {"question": "x" * 501},
        {"question": 7},
        {"question": "ok", "user_id": 1},
        ["a question"],
    ],
)
def test_a_malformed_question_is_a_422_and_runs_nothing(sent) -> None:
    opened: list = []
    response = client(service([reply(BILL)], opened=opened)).post("/api/v1/queries", json=sent)
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "invalid_request" and body["message"] and body["detail"]
    assert set(body["detail"][0]) == {"where", "why"}
    assert opened == [], "a malformed request must not open the pipeline"


def test_what_was_sent_is_not_echoed_back_in_an_error() -> None:
    response = client(service([])).post("/api/v1/queries", json={"question": "ok", "password": "hunter2-sentinel"})
    assert response.status_code == 422 and "hunter2-sentinel" not in response.text


def test_a_body_that_is_not_json_is_a_422() -> None:
    response = client(service([])).post("/api/v1/queries", content=b"not json", headers={"content-type": "application/json"})
    assert response.status_code == 422 and response.json()["code"] == "invalid_request"


def test_a_question_of_exactly_the_longest_length_is_taken() -> None:
    assert ask(service([reply(BILL)]), "x" * 500).status_code == 200


def test_the_routes_are_only_under_the_version_prefix_and_a_miss_has_a_code() -> None:
    """IR-01, IR-05."""
    made = client(service([]))
    for path in ("/queries", "/schema", "/api/queries"):
        assert made.get(path).status_code == 404
    assert made.get("/api/v1/nothing").json() == {"code": "not_found", "message": "Not Found"}


# --------------------------------------------------------------------------
# 5xx: a fault, and nothing else
# --------------------------------------------------------------------------


def test_a_trace_that_could_not_be_stored_is_a_500_and_the_answer_is_not_returned() -> None:
    """Ruling 5, NFR-13."""
    response = ask(service([reply(BILL)], traces=Memory(refuse=ConnectionError("postgresql://u:hunter2@h/db gone"))))
    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "trace_not_persisted" and uuid.UUID(body["query_id"])
    assert set(body) == {"code", "message", "query_id"}
    assert "hunter2" not in response.text and "rows" not in body and "trace" not in body


def test_an_unforeseen_fault_is_a_500_that_says_nothing_of_its_cause() -> None:
    class Broken(Runs):
        def run(self, validated):
            raise RuntimeError("secret-sentinel in an exception message")

    response = ask(service([reply(BILL)], executor=Broken()))
    assert response.status_code == 500
    assert response.json() == {"code": "internal_error", "message": "The server failed while handling this request."}
    assert "secret-sentinel" not in response.text


# --------------------------------------------------------------------------
# Not ready: the stack is up and something a question needs is missing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing",
    [ModelKeyMissing("OPENAI_API_KEY is not set, and this needs the model that writes SQL."),
     NoCurrentSnapshot("the application store holds no schema snapshot: nothing has been ingested"),
     SystemExit("api: eval/calibration.json is missing. Run: python -m app.calibrate")],
)  # fmt: skip
def test_a_pipeline_that_cannot_open_is_a_503_that_says_what_is_missing(missing) -> None:
    def cannot_open():
        raise missing

    response = ask(QueryService(cannot_open, Memory(), NoSchema()))
    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "not_ready" and body["message"] == str(missing.code if isinstance(missing, SystemExit) else missing)


def test_a_stored_query_can_be_read_when_the_pipeline_cannot_open() -> None:
    """Reading a trace needs no key, no warehouse and no model."""
    traces = Memory()
    asked = ask(service([reply(BILL)], traces=traces)).json()

    def cannot_open():
        raise ModelKeyMissing("no key")

    read = client(QueryService(cannot_open, traces, NoSchema())).get(f"/api/v1/queries/{asked['query_id']}")
    assert read.status_code == 200 and read.json()["trace"] == asked["trace"]


def test_the_pipeline_is_opened_at_the_first_question_and_once() -> None:
    opened: list = []
    made = service([reply(BILL), reply(BILL)], opened=opened)
    served = client(made)
    assert served.get("/api/v1/health").status_code == 200 and opened == []
    served.get(f"/api/v1/queries/{uuid.uuid4()}")
    assert opened == []
    assert served.post("/api/v1/queries", json={"question": "one?"}).status_code == 200
    assert served.post("/api/v1/queries", json={"question": "two?"}).status_code == 200
    assert opened == [1]


# --------------------------------------------------------------------------
# Two questions at once (NFR-06)
# --------------------------------------------------------------------------


def test_two_questions_in_flight_each_get_their_own_answer() -> None:
    """Neither request fails and neither returns the other's result."""
    plans = {
        "stores?": (located("store_sales", "store"), STORE_SQL, "store"),
        "addresses?": (located(*ADDRESS), BILL, "address"),
    }

    class Slow:
        model = "fake-slow"

        def complete(self, messages, shape) -> ModelReply:
            question = messages[1].content.rsplit("Question: ", 1)[1]
            time.sleep(0.05)
            return ModelReply(self.model, self.model, None, reply(plans[question][1]), 0, 0, 0, 0.0, 0)

    class Tells:
        def run(self, validated) -> Execution:
            time.sleep(0.02)
            kind = "store" if "store_sales" in validated.text else "address"
            return Execution(executed=validated.text, columns=("kind",), rows=((kind,),))

    traces = Memory()

    def open_orchestrator() -> Orchestrator:
        return Orchestrator(
            SNAPSHOT, SCHEMA_REF, lambda question: plans[question][0], Slow(), Tells(), keep=Keep(traces, CONTEXT, 1)
        )

    served = client(QueryService(open_orchestrator, traces, NoSchema()))
    results: list[tuple[str, int, dict]] = []

    def go(question: str) -> None:
        response = served.post("/api/v1/queries", json={"question": question})
        results.append((question, response.status_code, response.json()))

    threads = [threading.Thread(target=go, args=(question,)) for question in ["stores?", "addresses?"] * 6]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 12 and all(status == 200 for _, status, _ in results)
    for question, _, body in results:
        assert body["trace"]["question"] == question
        assert body["rows"] == [[plans[question][2]]]
        assert body["trace"]["execution"]["sql"] == plans[question][1]
    ids = {body["query_id"] for _, _, body in results}
    assert len(ids) == 12 and set(traces.bodies) == {uuid.UUID(one) for one in ids}


# --------------------------------------------------------------------------
# The schema graph (IR-06, DD-18)
# --------------------------------------------------------------------------


def test_the_schema_is_served_as_table_nodes_and_key_edges(scratch_database) -> None:
    snapshots = SnapshotStore(scratch_database)
    stored = snapshots.save(SMALL, "test")
    response = client(service([], snapshots=snapshots)).get("/api/v1/schema")
    assert response.status_code == 200
    body = response.json()
    assert (body["snapshot_id"], body["hash"]) == (stored.id, stored.hash)
    assert [node["id"] for node in body["nodes"]] == ["sale", "return", "shop"]
    sale = body["nodes"][0]
    assert [column["name"] for column in sale["columns"]] == ["item", "ticket", "shop_key", "price"]
    flags = {column["name"]: (column["primary_key"], column["foreign_key"]) for column in sale["columns"]}
    assert flags == {"item": (True, False), "ticket": (True, False), "shop_key": (False, True), "price": (False, False)}
    assert sale["columns"][3]["readable"] == "what it's sold for"
    edges = {edge["id"]: edge for edge in body["edges"]}
    assert set(edges) == {"sale.shop_key=shop.key", "return.item=sale.item / return.ticket=sale.ticket"}
    two = edges["return.item=sale.item / return.ticket=sale.ticket"]
    assert (two["from_columns"], two["to_columns"], two["source"]) == (["item", "ticket"], ["item", "ticket"], "overlay")
    assert two["note"] == "measured: every return matches one sale" and edges["sale.shop_key=shop.key"]["source"] == "catalog"


def test_the_schema_before_anything_is_ingested_is_a_503_that_says_what_to_run() -> None:
    response = client(service([])).get("/api/v1/schema")
    assert response.status_code == 503
    assert response.json()["code"] == "not_ready" and "python -m app.ingest_schema" in response.json()["message"]


def test_every_response_body_is_declared(scratch_database) -> None:
    """IR-11: nothing untyped crosses the API. The OpenAPI description
    names a schema for every response of the three routes."""
    described = create_app(SETTINGS).openapi()["paths"]
    for path, method in (("/api/v1/queries", "post"), ("/api/v1/queries/{query_id}", "get"), ("/api/v1/schema", "get")):
        responses = described[path][method]["responses"]
        for status in ("200", "422", "500", "503"):
            assert "$ref" in responses[status]["content"]["application/json"]["schema"], (path, status)
