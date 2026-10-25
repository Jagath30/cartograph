"""Each user's own history (step 11, criterion 12, FR-34, IR-08, DR-08).

    GET  /api/v1/queries          the caller's newest 100, newest first
    GET  /api/v1/queries/{id}     one of the caller's own; anyone else's is 404
    POST /api/v1/queries          asked as the caller, stored under the caller

Asking, the list and reading a stored answer need a session; the schema,
health and readiness do not (the brief's decision 5).
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from api_client import served, service
from memory_stores import Memory, MemoryAccounts
from tracing import document, reply

from app.shell.accounts import Accounts
from app.shell.trace_store import HISTORY_LIMIT

BILL = "SELECT ca.ca_state FROM catalog_sales cs JOIN customer_address ca ON cs.cs_bill_addr_sk = ca.ca_address_sk"
ADA = {"email": "ada@example.com", "password": "correct horse battery"}
BOB = {"email": "bob@example.com", "password": "another horse battery"}


@pytest.fixture(params=["memory", "postgres"])
def accounts(request):
    if request.param == "memory":
        return MemoryAccounts()
    return Accounts(request.getfixturevalue("scratch_database"))


def two_users(accounts, script=(), traces=None, opened=None):
    """Ada's client and Bob's, over one service and one store."""
    traces = traces if traces is not None else Memory()
    made = service(list(script), traces=traces, opened=opened)
    clients = []
    for who in (ADA, BOB):
        web = served(made, accounts=accounts)
        web.post("/api/v1/auth/register", json=who)
        assert web.post("/api/v1/auth/login", json=who).status_code == 200
        clients.append(web)
    return clients[0], clients[1], traces


# --------------------------------------------------------------------------
# What needs a session
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [("post", "/api/v1/queries", {"question": "Where are buyers billed?"}), ("get", "/api/v1/queries", None),
     ("get", f"/api/v1/queries/{uuid.uuid4()}", None), ("post", f"/api/v1/queries/{uuid.uuid4()}/rerun", None)],
)  # fmt: skip
def test_without_a_session_asking_the_list_reading_and_rerunning_are_refused(accounts, method, path, body) -> None:
    opened: list = []
    web = served(service([reply(BILL)], opened=opened), accounts=accounts)
    response = getattr(web, method)(path, json=body) if body else getattr(web, method)(path)
    assert response.status_code == 401
    assert response.json() == {"code": "not_signed_in", "message": "Sign in to do this."}
    assert opened == [], "a refused request must not open the pipeline"


@pytest.mark.parametrize("path", ["/api/v1/health", "/api/v1/schema"])
def test_the_schema_and_health_stay_open(accounts, path) -> None:
    assert served(accounts=accounts).get(path).status_code in (200, 503)
    assert served(accounts=accounts).get(path).status_code != 401


# --------------------------------------------------------------------------
# Whose it is
# --------------------------------------------------------------------------


def test_a_question_is_stored_under_the_user_who_asked_it(accounts) -> None:
    ada, bob, traces = two_users(accounts, [reply(BILL), reply(BILL)])
    asked_by_ada = ada.post("/api/v1/queries", json={"question": "Ada asks?"}).json()
    asked_by_bob = bob.post("/api/v1/queries", json={"question": "Bob asks?"}).json()
    assert asked_by_ada["trace"]["user_id"] != asked_by_bob["trace"]["user_id"]
    assert traces.load(uuid.UUID(asked_by_ada["query_id"])).user_id == asked_by_ada["trace"]["user_id"]


def test_another_users_answer_is_a_404_the_same_as_one_that_does_not_exist(accounts) -> None:
    ada, bob, _ = two_users(accounts, [reply(BILL)])
    asked = ada.post("/api/v1/queries", json={"question": "Ada asks?"}).json()
    assert ada.get(f"/api/v1/queries/{asked['query_id']}").status_code == 200
    nobody = str(uuid.uuid4())
    theirs = bob.get(f"/api/v1/queries/{asked['query_id']}")
    nobodys = bob.get(f"/api/v1/queries/{nobody}")
    assert theirs.status_code == nobodys.status_code == 404
    assert theirs.json()["code"] == nobodys.json()["code"] == "not_found"
    assert theirs.json()["message"].replace(asked["query_id"], "ID") == nobodys.json()["message"].replace(nobody, "ID")
    assert "Ada asks?" not in theirs.text


def test_another_users_answer_cannot_be_rerun(accounts) -> None:
    ada, bob, _ = two_users(accounts, [reply(BILL)])
    asked = ada.post("/api/v1/queries", json={"question": "Ada asks?"}).json()
    assert bob.post(f"/api/v1/queries/{asked['query_id']}/rerun").status_code == 404


def test_the_local_users_queries_are_no_ones_to_read(accounts) -> None:
    """The queries stored before step 11 belong to the local user, who
    cannot log in (the brief's decision 5)."""
    traces = Memory()
    old = document([reply(BILL)], "catalog_sales", "customer_address").model_copy(update={"user_id": 1, "query_id": uuid.uuid4()})
    traces.save(old)
    ada, _, _ = two_users(accounts, traces=traces)
    assert ada.get(f"/api/v1/queries/{old.query_id}").status_code == 404
    assert ada.get("/api/v1/queries").json() == {"queries": []}


# --------------------------------------------------------------------------
# The list
# --------------------------------------------------------------------------


def test_history_is_ones_own_newest_first(accounts) -> None:
    ada, bob, _ = two_users(accounts, [reply(BILL), reply(status="not_answerable"), reply(BILL), reply(BILL)])
    first = ada.post("/api/v1/queries", json={"question": "First?"}).json()
    second = ada.post("/api/v1/queries", json={"question": "Second?"}).json()
    bobs = bob.post("/api/v1/queries", json={"question": "Bob's?"}).json()
    third = ada.post("/api/v1/queries", json={"question": "Third?"}).json()

    listed = ada.get("/api/v1/queries")
    assert listed.status_code == 200
    rows = listed.json()["queries"]
    assert [row["query_id"] for row in rows] == [third["query_id"], second["query_id"], first["query_id"]]
    assert [row["question"] for row in rows] == ["Third?", "Second?", "First?"]
    assert [row["status"] for row in rows] == ["answered", "not_answerable", "answered"]
    assert set(rows[0]) == {"query_id", "question", "created_at", "status", "had_ambiguity"}
    assert [row["query_id"] for row in bob.get("/api/v1/queries").json()["queries"]] == [bobs["query_id"]]


def test_history_marks_an_answer_a_choice_without_basis_touched(accounts) -> None:
    ada, _, traces = two_users(accounts, [reply(BILL)])
    asked = ada.post("/api/v1/queries", json={"question": "Ada asks?"}).json()
    (row,) = ada.get("/api/v1/queries").json()["queries"]
    assert row["had_ambiguity"] is asked["trace"]["had_ambiguity"]
    stored = traces.load(uuid.UUID(asked["query_id"]))
    marked = stored.model_copy(
        update={"had_ambiguity": True, "query_id": uuid.uuid4(), "created_at": stored.created_at + timedelta(minutes=1)}
    )
    traces.save(marked)
    assert [row["had_ambiguity"] for row in ada.get("/api/v1/queries").json()["queries"]] == [True, row["had_ambiguity"]]


def test_history_lists_the_newest_100_and_no_more(accounts) -> None:
    """The owner's ruling 7: no paging, a known limit."""
    traces = Memory()
    ada, _, _ = two_users(accounts, traces=traces)
    owner = accounts.log_in(ADA["email"], ADA["password"], datetime.now(UTC))[1].user_id
    base = document([reply(BILL)], "catalog_sales", "customer_address")
    start = datetime(2026, 10, 1, tzinfo=UTC)
    for n in range(HISTORY_LIMIT + 5):
        traces.save(base.model_copy(update={"user_id": owner, "query_id": uuid.uuid4(), "question": f"Q{n}?",
                                            "created_at": start + timedelta(minutes=n)}))  # fmt: skip
    rows = ada.get("/api/v1/queries").json()["queries"]
    assert HISTORY_LIMIT == 100 and len(rows) == 100
    assert rows[0]["question"] == "Q104?" and rows[-1]["question"] == "Q5?"

