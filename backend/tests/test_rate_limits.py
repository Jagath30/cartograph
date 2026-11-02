"""The rate limits (FR-36, item 112, step 14): login, registration,
questions and reruns, counted in Redis, every 429 with Retry-After and
words saying which limit it is and when it lifts; Redis down, nothing is
limited."""

import logging
import uuid

import pytest
from api_client import ADA, client, served, service
from fake_redis import DownRedis, FakeRedis
from memory_stores import Memory, MemoryAccounts
from tracing import document, reply

from app.core.limits import (
    LIMITS,
    LOGIN_CEILING,
    LOGIN_FAILURES,
    QUESTIONS,
    REGISTRATIONS,
    RERUNS,
    email_subject,
    lifts_in,
)
from app.shell.counters import Counters

PASSWORD = "correct horse battery staple"
REAL = "ada@example.com"
UNKNOWN = "nobody@example.com"


def registered(redis=None) -> tuple[MemoryAccounts, object]:
    accounts = MemoryAccounts()
    accounts.register(REAL, PASSWORD)
    return accounts, served(accounts=accounts, redis=redis)


def login(web, email, password="wrong password, long enough"):
    return web.post("/api/v1/auth/login", json={"email": email, "password": password})


def assert_limited(response, code: str, window: int) -> None:
    assert response.status_code == 429, response.text
    assert response.json()["code"] == code
    seconds = int(response.headers["Retry-After"])
    assert 1 <= seconds <= window
    assert response.json()["message"].endswith(f"again in {lifts_in(seconds)}.")


# --------------------------------------------------------------------------
# The limits themselves
# --------------------------------------------------------------------------


def test_the_limits_are_the_briefs() -> None:
    assert (LOGIN_FAILURES.count, LOGIN_FAILURES.window_seconds) == (5, 15 * 60)
    assert (LOGIN_CEILING.count, LOGIN_CEILING.window_seconds) == (300, 60 * 60)
    assert (REGISTRATIONS.count, REGISTRATIONS.window_seconds) == (30, 60 * 60)
    assert (QUESTIONS.count, QUESTIONS.window_seconds) == (10, 60)
    assert (RERUNS.count, RERUNS.window_seconds) == (30, 60 * 60)


def test_each_limit_has_its_own_code_and_none_is_the_daily_limit() -> None:
    codes = [limit.code for limit in LIMITS]
    assert len(set(codes)) == len(codes) == 5
    assert "daily_limit" not in codes


def test_a_key_holds_the_emails_hash_never_the_email() -> None:
    key = LOGIN_FAILURES.key(email_subject("  Ada@Example.com "))
    assert "ada" not in key.lower() and "example" not in key
    assert key.endswith(email_subject("ada@example.com"))


def test_lifts_in_words() -> None:
    assert lifts_in(1) == "1 second"
    assert lifts_in(59) == "59 seconds"
    assert lifts_in(60) == "1 minute"
    assert lifts_in(61) == "2 minutes"


# --------------------------------------------------------------------------
# Login
# --------------------------------------------------------------------------


@pytest.mark.parametrize("email", [REAL, UNKNOWN])
def test_five_failures_then_429_until_the_window_ends(email) -> None:
    redis = FakeRedis()
    _, web = registered(redis)
    for _ in range(5):
        assert login(web, email).status_code == 401
    assert_limited(login(web, email), "login_limit", 15 * 60)
    redis.advance(15 * 60 - 1)
    assert login(web, email).status_code == 429
    redis.advance(1)
    assert login(web, email).status_code == 401


def test_the_message_is_the_same_for_a_real_and_an_unknown_email() -> None:
    redis = FakeRedis()
    _, web = registered(redis)
    for _ in range(5):
        login(web, REAL), login(web, UNKNOWN)
    real, unknown = login(web, REAL), login(web, UNKNOWN)
    assert real.status_code == unknown.status_code == 429
    assert real.json() == unknown.json()
    assert real.headers["Retry-After"] == unknown.headers["Retry-After"]


def test_locked_even_the_right_password_is_refused_and_nothing_is_hashed(monkeypatch) -> None:
    accounts, web = registered()
    for _ in range(5):
        login(web, REAL)
    called = []
    monkeypatch.setattr(accounts, "log_in", lambda *a: called.append(1))
    assert login(web, REAL, PASSWORD).status_code == 429
    assert called == []


def test_a_success_clears_that_emails_failures() -> None:
    _, web = registered()
    for _ in range(4):
        login(web, REAL)
    assert login(web, REAL, PASSWORD).status_code == 200
    for _ in range(5):
        assert login(web, REAL).status_code == 401
    assert login(web, REAL).status_code == 429


def test_one_emails_failures_do_not_lock_another() -> None:
    _, web = registered()
    for _ in range(6):
        login(web, UNKNOWN)
    assert login(web, REAL, PASSWORD).status_code == 200


def test_the_hours_ceiling_counts_every_login_from_everyone() -> None:
    redis = FakeRedis()
    _, web = registered(redis)
    redis.values[LOGIN_CEILING.key("all")] = b"300"
    redis.expires[LOGIN_CEILING.key("all")] = 1800
    assert_limited(login(web, REAL, PASSWORD), "login_ceiling", 60 * 60)


def test_a_key_in_redis_never_holds_an_email() -> None:
    redis = FakeRedis()
    _, web = registered(redis)
    login(web, REAL), login(web, UNKNOWN), login(web, REAL, PASSWORD)
    assert redis.keys_held()
    assert not any("@" in key or "example" in key for key in redis.keys_held())


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


def test_thirty_registrations_an_hour_then_429() -> None:
    redis = FakeRedis()
    web = served(redis=redis)
    for number in range(30):
        made = web.post("/api/v1/auth/register", json={"email": f"user{number}@example.com", "password": PASSWORD})
        assert made.status_code == 201
    refused = web.post("/api/v1/auth/register", json={"email": "late@example.com", "password": PASSWORD})
    assert_limited(refused, "register_limit", 60 * 60)
    redis.advance(60 * 60)
    assert web.post("/api/v1/auth/register", json={"email": "late@example.com", "password": PASSWORD}).status_code == 201


# --------------------------------------------------------------------------
# Questions and reruns
# --------------------------------------------------------------------------


BILL = "SELECT ca.ca_state FROM catalog_sales cs JOIN customer_address ca ON cs.cs_bill_addr_sk = ca.ca_address_sk"
ADDRESS = ("catalog_sales", "customer_address")


def declined():
    return reply(status="not_answerable")


def test_ten_questions_a_minute_then_429_and_the_eleventh_is_not_run() -> None:
    redis = FakeRedis()
    traces = Memory()
    made = service([declined() for _ in range(11)], traces=traces)
    web = client(made, redis=redis)
    for _ in range(10):
        assert web.post("/api/v1/queries", json={"question": "How many?"}).status_code == 200
    stored = len(traces.bodies)
    assert_limited(web.post("/api/v1/queries", json={"question": "How many?"}), "minute_limit", 60)
    assert len(traces.bodies) == stored
    redis.advance(60)
    assert web.post("/api/v1/queries", json={"question": "How many?"}).status_code == 200


def test_one_users_minute_does_not_limit_another() -> None:
    redis = FakeRedis()
    for _ in range(10):
        redis.incr(QUESTIONS.key(f"user:{ADA.user_id}"))
    redis.expire(QUESTIONS.key(f"user:{ADA.user_id}"), 60)
    other = type(ADA)(2, "bob@example.com")
    made = service([declined()])
    assert client(made, user=other, redis=redis).post("/api/v1/queries", json={"question": "How many?"}).status_code == 200


def test_thirty_reruns_an_hour_then_429() -> None:
    redis = FakeRedis()
    traces = Memory()
    stored = document([declined()], *ADDRESS)
    traces.save(stored)
    web = client(service([], traces=traces), redis=redis)
    path = f"/api/v1/queries/{stored.query_id}/rerun"
    for _ in range(30):
        assert web.post(path).status_code == 409  # nothing to rerun, and counted
    assert_limited(web.post(path), "rerun_limit", 60 * 60)


def test_a_rerun_of_nothing_is_still_counted_and_404_is_too() -> None:
    redis = FakeRedis()
    web = client(service([]), redis=redis)
    for _ in range(30):
        assert web.post(f"/api/v1/queries/{uuid.uuid4()}/rerun").status_code in (404, 503)
    assert web.post(f"/api/v1/queries/{uuid.uuid4()}/rerun").status_code == 429


# --------------------------------------------------------------------------
# Redis down: fail open
# --------------------------------------------------------------------------


def test_redis_down_nothing_is_limited_and_a_line_is_logged(caplog) -> None:
    down = DownRedis()
    _, web = registered(down)
    with caplog.at_level(logging.WARNING, logger="cartograph.limits"):
        for _ in range(8):
            assert login(web, UNKNOWN).status_code == 401
        assert login(web, REAL, PASSWORD).status_code == 200
    assert "Redis did not answer (ConnectionError)" in caplog.text
    assert "@" not in caplog.text

    made = service([declined() for _ in range(12)])
    asking = client(made, redis=down)
    for _ in range(12):
        assert asking.post("/api/v1/queries", json={"question": "How many?"}).status_code == 200


def test_redis_down_registration_and_reruns_go_on() -> None:
    down = DownRedis()
    web = served(redis=down)
    for number in range(31):
        assert web.post("/api/v1/auth/register", json={"email": f"u{number}@example.com", "password": PASSWORD}).status_code == 201
    rerun = client(service([]), redis=down)
    for _ in range(31):
        assert rerun.post(f"/api/v1/queries/{uuid.uuid4()}/rerun").status_code != 429


def test_every_count_reads_redis_once_and_fails_open_on_each_kind_of_error() -> None:
    class Broken:
        def __getattr__(self, name):
            def fail(*args, **kwargs):
                import redis

                raise redis.TimeoutError("Timeout reading from socket")

            return fail

    counters = Counters(Broken())
    assert counters.count(QUESTIONS, "user:1") is None
    assert counters.reached(LOGIN_FAILURES, "x") is None
    counters.clear(LOGIN_FAILURES, "x")
