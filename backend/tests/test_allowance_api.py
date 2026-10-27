"""The deployed demonstration's allowance through the API (NFR-25, step
12): a question past the month's budget is a 503, past the user's day a
429, each with a plain message, and neither is run or stored. And the
pipeline opened in the background once the server has started (the
owner's ruling 3).

The real orchestrator with a scripted model; the stores in memory.
"""

import threading
import time
import uuid
from datetime import UTC, datetime

from api_client import ADA, SETTINGS, NoSchema, client, service
from fastapi.testclient import TestClient
from memory_stores import Memory
from tracing import document, reply

from app.api import get_service
from app.auth import current_user, now
from app.core.budget import BUDGET_SPENT, Allowance, daily_limit_message
from app.main import create_app
from app.shell.accounts import SignedIn
from app.shell.model_client import ModelKeyMissing
from app.shell.query_service import QueryService

BILL = "SELECT ca.ca_state FROM catalog_sales cs JOIN customer_address ca ON cs.cs_bill_addr_sk = ca.ca_address_sk"
ADDRESS = ("catalog_sales", "customer_address")
# The day api_client's clock keeps its questions on.
NOON = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
DEMO = Allowance(monthly_usd=2.00, daily_questions=30, question_bound_usd=0.03)


def at_noon(made: QueryService, user: SignedIn | None = None):
    served = client(made, **({"user": user} if user else {}))
    served.app.dependency_overrides[now] = lambda: NOON
    return served


def spent(traces: Memory, dollars: float, when: datetime = NOON) -> None:
    """A question already asked, by someone else, at this cost."""
    old = document([reply(BILL)], *ADDRESS).model_copy(
        update={"query_id": uuid.uuid4(), "user_id": 99, "created_at": when, "cost_usd": dollars}
    )
    traces.save(old)


def test_a_question_within_the_allowance_is_answered() -> None:
    traces = Memory()
    spent(traces, 1.97)
    response = at_noon(service([reply(BILL)], traces=traces, allowance=DEMO)).post("/api/v1/queries", json={"question": "where?"})
    assert response.status_code == 200 and response.json()["status"] == "answered"


def test_a_question_that_could_pass_the_months_budget_is_a_503_and_runs_nothing() -> None:
    traces = Memory()
    spent(traces, 1.98)
    opened: list = []
    response = at_noon(service([reply(BILL)], traces=traces, opened=opened, allowance=DEMO)).post(
        "/api/v1/queries", json={"question": "where?"}
    )
    assert response.status_code == 503
    assert response.json() == {"code": "budget_spent", "message": BUDGET_SPENT}
    assert len(traces.bodies) == 1 and opened == []


def test_last_months_spend_is_not_this_months() -> None:
    traces = Memory()
    spent(traces, 1.99, when=datetime(2026, 9, 30, 23, 59, tzinfo=UTC))
    response = at_noon(service([reply(BILL)], traces=traces, allowance=DEMO)).post("/api/v1/queries", json={"question": "where?"})
    assert response.status_code == 200


def test_past_the_users_day_is_a_429_and_another_user_may_still_ask() -> None:
    traces = Memory()
    two = Allowance(monthly_usd=2.00, daily_questions=2, question_bound_usd=0.03)
    made = service([reply(BILL)] * 3, traces=traces, allowance=two)
    ada = at_noon(made)
    assert [ada.post("/api/v1/queries", json={"question": f"{n}?"}).status_code for n in (1, 2)] == [200, 200]
    third = ada.post("/api/v1/queries", json={"question": "3?"})
    assert third.status_code == 429
    assert third.json() == {"code": "daily_limit", "message": daily_limit_message(2)}
    assert len(traces.bodies) == 2
    bob = at_noon(made, user=SignedIn(2, "bob@example.com"))
    assert bob.post("/api/v1/queries", json={"question": "mine?"}).status_code == 200


def test_without_an_allowance_nothing_is_counted() -> None:
    """The service the commands' tests build: no allowance, no check."""
    traces = Memory()
    spent(traces, 50.0)
    assert at_noon(service([reply(BILL)], traces=traces)).post("/api/v1/queries", json={"question": "x?"}).status_code == 200


def test_a_question_running_holds_its_bound_until_it_ends() -> None:
    """Two asked at once: the second is refused while the first still
    holds the month's last room, and admitted once it has ended."""
    traces = Memory()
    spent(traces, 1.95)
    release = threading.Event()
    inside = threading.Event()

    class Held:
        def answer_and_keep(self, question, user_id):
            inside.set()
            release.wait(5)
            return "kept"

    made = QueryService(lambda: Held(), traces, NoSchema(), allowance=DEMO)
    first = threading.Thread(target=lambda: made.ask("first?", 1, NOON))
    first.start()
    assert inside.wait(5)
    # 1.95 + the first's 0.03 + this one's 0.03 = 2.01.
    refused = at_noon(made).post("/api/v1/queries", json={"question": "second?"})
    assert refused.status_code == 503 and refused.json()["code"] == "budget_spent"
    release.set()
    first.join(5)
    assert made.ask("third?", 1, NOON) == "kept"


def test_a_users_question_still_running_counts_against_their_day() -> None:
    release = threading.Event()
    inside = threading.Event()

    class Held:
        def answer_and_keep(self, question, user_id):
            inside.set()
            release.wait(5)
            return "kept"

    one = Allowance(monthly_usd=2.00, daily_questions=1, question_bound_usd=0.03)
    made = QueryService(lambda: Held(), Memory(), NoSchema(), allowance=one)
    first = threading.Thread(target=lambda: made.ask("first?", ADA.user_id, NOON))
    first.start()
    assert inside.wait(5)
    refused = at_noon(made).post("/api/v1/queries", json={"question": "second?"})
    release.set()
    first.join(5)
    assert refused.status_code == 429 and refused.json()["code"] == "daily_limit"


# --------------------------------------------------------------------------
# Opened in the background at start (the owner's ruling 3)
# --------------------------------------------------------------------------


def started(made: QueryService) -> TestClient:
    """Used as a context manager, so the server starts, as uvicorn starts
    it; `client` never starts it."""
    app = create_app(SETTINGS)
    app.dependency_overrides[get_service] = lambda: made
    app.dependency_overrides[current_user] = lambda: ADA
    return TestClient(app, raise_server_exceptions=False)


def waited(condition, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def test_the_pipeline_is_opened_once_the_server_has_started_with_no_question() -> None:
    opened: list = []
    with started(service([reply(BILL)], opened=opened)) as served:
        assert waited(lambda: opened == [1])
        assert served.get("/api/v1/health").status_code == 200
    assert opened == [1]


def test_a_question_that_arrives_first_waits_for_the_opening_and_it_happens_once() -> None:
    opening = threading.Event()
    go_on = threading.Event()
    opened: list = []
    inner = service([reply(BILL)], opened=opened)

    def slow_open():
        opening.set()
        go_on.wait(5)
        return inner._open()

    made = QueryService(slow_open, inner._traces, NoSchema())
    with started(made) as served:
        assert opening.wait(5)
        answers: list = []
        asking = threading.Thread(target=lambda: answers.append(served.post("/api/v1/queries", json={"question": "where?"})))
        asking.start()
        time.sleep(0.1)
        assert answers == []
        go_on.set()
        asking.join(5)
    assert answers[0].status_code == 200 and opened == [1]


def test_an_opening_that_is_not_ready_at_start_is_said_to_the_question_as_before() -> None:
    tries: list = []

    def cannot_open():
        tries.append(1)
        raise ModelKeyMissing("OPENAI_API_KEY is not set, and this needs the model that writes SQL.")

    with started(QueryService(cannot_open, Memory(), NoSchema())) as served:
        assert waited(lambda: tries == [1])
        assert served.get("/api/v1/health").status_code == 200
        response = served.post("/api/v1/queries", json={"question": "where?"})
    assert response.status_code == 503 and response.json()["code"] == "not_ready"
    assert tries == [1, 1]
