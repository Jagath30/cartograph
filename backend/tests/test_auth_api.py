"""Accounts and sessions through the API (step 11, criterion 12, IR-09,
NFR-10, DR-07, T-08).

    POST /api/v1/auth/register    {email, password} -> 201 {email}
    POST /api/v1/auth/login       {email, password} -> 200 {email}, the cookie
    POST /api/v1/auth/logout      -> 204, the session revoked, the cookie cleared
    GET  /api/v1/auth/me          -> 200 {email}, or 401

Every test that takes `accounts` runs twice: on the accounts kept in
memory, and on the real store on a scratch database (skipped where there
is none, as in CI).
"""

import hashlib
import logging
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from api_client import URLS, served, service
from memory_stores import MemoryAccounts
from tracing import reply

from app.config import Settings
from app.main import create_app
from app.shell import accounts as credentials
from app.shell.accounts import Accounts

COOKIE = "cartograph_session"
ADA = {"email": "ada@example.com", "password": "correct horse battery"}
WRONG = "the same message for an unknown email and a wrong password"
BILL = "SELECT ca.ca_state FROM catalog_sales cs JOIN customer_address ca ON cs.cs_bill_addr_sk = ca.ca_address_sk"
LOGIN_AT = datetime(2026, 10, 25, 9, 0, tzinfo=UTC)


@pytest.fixture(params=["memory", "postgres"])
def accounts(request):
    if request.param == "memory":
        return MemoryAccounts()
    return Accounts(request.getfixturevalue("scratch_database"))


def signed_in(accounts, **arguments):
    """A client that has registered Ada and logged her in."""
    web = served(accounts=accounts, **arguments)
    assert web.post("/api/v1/auth/register", json=ADA).status_code == 201
    assert web.post("/api/v1/auth/login", json=ADA).status_code == 200
    return web


def set_cookie(response) -> str:
    (header,) = [value for name, value in response.headers.multi_items() if name == "set-cookie"]
    return header


# --------------------------------------------------------------------------
# Register
# --------------------------------------------------------------------------


def test_register_makes_an_account_and_says_its_email(accounts) -> None:
    response = served(accounts=accounts).post("/api/v1/auth/register", json={**ADA, "email": "  Ada@Example.COM "})
    assert response.status_code == 201
    assert response.json() == {"email": "ada@example.com"}
    assert "set-cookie" not in response.headers, "registering does not sign in"


def test_an_email_already_held_is_refused_whatever_its_case(accounts) -> None:
    web = served(accounts=accounts)
    assert web.post("/api/v1/auth/register", json=ADA).status_code == 201
    again = web.post("/api/v1/auth/register", json={**ADA, "email": "ADA@example.com", "password": "another one"})
    assert again.status_code == 409
    assert again.json() == {"code": "email_taken", "message": "An account with this email already exists."}


def test_the_local_users_email_cannot_be_registered(accounts) -> None:
    response = served(accounts=accounts).post(
        "/api/v1/auth/register", json={"email": "local@cartograph.invalid", "password": "correct horse"}
    )
    assert response.status_code == 409


@pytest.mark.parametrize(
    "sent",
    [{"email": "ada", "password": "correct horse"}, {"email": "ada@example.com", "password": "short"},
     {"email": "ada@example.com", "password": "p" * 129}, {"email": "ada@example.com"}, {"password": "correct horse"},
     {**ADA, "admin": True}, {"email": 7, "password": "correct horse"}],
)  # fmt: skip
def test_a_malformed_registration_is_a_422_and_makes_nothing(accounts, sent) -> None:
    web = served(accounts=accounts)
    response = web.post("/api/v1/auth/register", json=sent)
    assert response.status_code == 422 and response.json()["code"] == "invalid_request"
    assert "correct horse" not in response.text and "p" * 129 not in response.text
    assert web.post("/api/v1/auth/login", json={"email": "ada@example.com", "password": "correct horse"}).status_code == 401


def test_a_password_too_long_is_refused_before_any_hashing(accounts, monkeypatch) -> None:
    """The owner's ruling 4, at both doors."""
    web = served(accounts=accounts)
    calls: list = []

    class Recorder:
        def hash(self, password):
            calls.append("hash")

        def verify(self, stored, password):
            calls.append("verify")

    monkeypatch.setattr(credentials, "_hasher", Recorder())
    assert web.post("/api/v1/auth/register", json={**ADA, "password": "p" * 129}).status_code == 422
    assert web.post("/api/v1/auth/login", json={**ADA, "password": "p" * 129}).status_code == 422
    assert calls == []


def test_a_password_of_128_characters_is_taken_and_logs_in(accounts) -> None:
    web = served(accounts=accounts)
    longest = {**ADA, "password": "p" * 128}
    assert web.post("/api/v1/auth/register", json=longest).status_code == 201
    assert web.post("/api/v1/auth/login", json=longest).status_code == 200


# --------------------------------------------------------------------------
# Log in
# --------------------------------------------------------------------------


def test_the_right_password_logs_in_and_the_session_says_who(accounts) -> None:
    web = served(accounts=accounts)
    web.post("/api/v1/auth/register", json=ADA)
    response = web.post("/api/v1/auth/login", json={**ADA, "email": " ADA@example.com"})
    assert response.status_code == 200 and response.json() == {"email": "ada@example.com"}
    assert web.cookies.get(COOKIE)
    assert web.get("/api/v1/auth/me").json() == {"email": "ada@example.com"}


@pytest.mark.parametrize(
    "sent",
    [{**ADA, "password": "correct horse batterz"}, {**ADA, "email": "nobody@example.com"},
     {"email": "local@cartograph.invalid", "password": "correct horse battery"}, {**ADA, "password": "x"}],
)  # fmt: skip
def test_a_failed_login_says_the_same_thing_whatever_failed(accounts, sent) -> None:
    """An unknown email, a wrong password, and the local user, who has no
    password: one status, one body, no cookie."""
    web = served(accounts=accounts)
    web.post("/api/v1/auth/register", json=ADA)
    response = web.post("/api/v1/auth/login", json=sent)
    assert response.status_code == 401
    assert response.json() == {"code": "invalid_credentials", "message": "The email or password is not right."}, WRONG
    assert "set-cookie" not in response.headers and not web.cookies


def test_without_a_session_no_one_is_signed_in(accounts) -> None:
    response = served(accounts=accounts).get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json() == {"code": "not_signed_in", "message": "Sign in to do this."}


@pytest.mark.parametrize("token", ["", "x", "not-a-token-anyone-was-given-0123456789abcdef", "%00", "a" * 4000])
def test_a_token_no_one_was_given_is_refused(accounts, token) -> None:
    web = signed_in(accounts)
    web.cookies.set(COOKIE, token)
    assert web.get("/api/v1/auth/me").status_code == 401


# --------------------------------------------------------------------------
# The cookie
# --------------------------------------------------------------------------


def test_the_cookie_is_httponly_lax_on_every_path_for_this_host_only_for_seven_days(accounts) -> None:
    web = served(accounts=accounts)
    web.post("/api/v1/auth/register", json=ADA)
    header = set_cookie(web.post("/api/v1/auth/login", json=ADA))
    parts = [part.strip() for part in header.split(";")]
    assert parts[0].startswith(f"{COOKIE}=")
    attributes = {part.split("=")[0].lower(): part.split("=", 1)[1] if "=" in part else True for part in parts[1:]}
    assert attributes == {"httponly": True, "max-age": "604800", "path": "/", "samesite": "lax"}
    assert "domain" not in header.lower() and "secure" not in header.lower()


def test_the_cookie_is_secure_when_the_setting_says_so(accounts) -> None:
    web = served(accounts=accounts, secure=True)
    web.post("/api/v1/auth/register", json=ADA)
    header = set_cookie(web.post("/api/v1/auth/login", json=ADA))
    assert "Secure" in [part.strip() for part in header.split(";")]


def test_the_cookie_is_secure_unless_a_setting_says_otherwise(monkeypatch) -> None:
    """The default, read with no variable set: the compose file sets it
    false for local development, and that must not hide the default."""
    monkeypatch.delenv("SESSION_COOKIE_SECURE", raising=False)
    assert Settings(**URLS).session_cookie_secure is True
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    assert Settings(**URLS).session_cookie_secure is False


def test_the_token_is_stored_only_as_its_sha256(scratch_database) -> None:
    web = signed_in(Accounts(scratch_database))
    token = web.cookies.get(COOKIE)
    with psycopg.connect(scratch_database) as connection:
        held = connection.execute("select token_sha256 from sessions").fetchall()
        everything = connection.execute(
            "select coalesce(string_agg(t::text, ' '), '') from ("
            " select row_to_json(s)::text as t from sessions s union all select row_to_json(u)::text from users u) rows"
        ).fetchone()[0]
    assert held == [(hashlib.sha256(token.encode()).hexdigest(),)]
    assert token not in everything
    assert ADA["password"] not in everything


def test_the_password_is_stored_only_as_its_argon2id_hash(scratch_database) -> None:
    served(accounts=Accounts(scratch_database)).post("/api/v1/auth/register", json=ADA)
    with psycopg.connect(scratch_database) as connection:
        (hashed,) = connection.execute("select password_hash from users where email = %s", (ADA["email"],)).fetchone()
    assert hashed.startswith("$argon2id$") and ADA["password"] not in hashed


# --------------------------------------------------------------------------
# Log out, expiry, revocation
# --------------------------------------------------------------------------


def test_logout_revokes_the_session_and_clears_the_cookie(accounts) -> None:
    web = signed_in(accounts)
    token = web.cookies.get(COOKIE)
    response = web.post("/api/v1/auth/logout")
    assert response.status_code == 204 and response.content == b""
    header = set_cookie(response)
    assert header.startswith(f'{COOKIE}=""') and "Max-Age=0" in header and "Path=/" in header and "HttpOnly" in header
    assert web.get("/api/v1/auth/me").status_code == 401
    web.cookies.set(COOKIE, token)
    assert web.get("/api/v1/auth/me").status_code == 401, "a revoked token is refused"


def test_logout_without_a_session_is_a_204(accounts) -> None:
    assert served(accounts=accounts).post("/api/v1/auth/logout").status_code == 204


def test_logging_out_one_session_leaves_the_others(accounts) -> None:
    first = signed_in(accounts)
    second = served(accounts=accounts)
    assert second.post("/api/v1/auth/login", json=ADA).status_code == 200
    assert first.post("/api/v1/auth/logout").status_code == 204
    assert first.get("/api/v1/auth/me").status_code == 401
    assert second.get("/api/v1/auth/me").status_code == 200


def test_a_session_expires_seven_days_after_login_and_is_never_extended(accounts) -> None:
    at = [LOGIN_AT]
    web = signed_in(accounts, at=at)
    for later, status in [(timedelta(days=1), 200), (timedelta(days=6), 200), (timedelta(days=7) - timedelta(seconds=1), 200),
                          (timedelta(days=7), 401), (timedelta(days=30), 401)]:  # fmt: skip
        at[0] = LOGIN_AT + later
        assert web.get("/api/v1/auth/me").status_code == status, later


def test_an_expired_session_is_refused_on_a_route_that_needs_one(accounts) -> None:
    at = [LOGIN_AT]
    web = signed_in(accounts, at=at)
    at[0] = LOGIN_AT + timedelta(days=8)
    response = web.post("/api/v1/queries", json={"question": "Where are buyers billed?"})
    assert response.status_code == 401 and response.json()["code"] == "not_signed_in"


def test_a_revoked_session_is_refused_on_a_route_that_needs_one(accounts) -> None:
    web = signed_in(accounts)
    token = web.cookies.get(COOKIE)
    web.post("/api/v1/auth/logout")
    web.cookies.set(COOKIE, token)
    assert web.get("/api/v1/queries").status_code == 401


def test_a_session_revoked_in_the_store_is_refused(scratch_database) -> None:
    web = signed_in(Accounts(scratch_database))
    with psycopg.connect(scratch_database) as connection:
        connection.execute("update sessions set revoked_at = now()")
    assert web.get("/api/v1/auth/me").status_code == 401


def test_a_session_expired_in_the_store_is_refused(scratch_database) -> None:
    """On the server's own clock, which nothing in the test moves."""
    web = signed_in(Accounts(scratch_database))
    assert web.get("/api/v1/auth/me").status_code == 200
    with psycopg.connect(scratch_database) as connection:
        connection.execute("update sessions set created_at = now() - interval '8 days', expires_at = now() - interval '1 day'")
    assert web.get("/api/v1/auth/me").status_code == 401


# --------------------------------------------------------------------------
# Cross-site requests (T-08, the brief's decision 3)
# --------------------------------------------------------------------------

FOREIGN = ["https://evil.example", "null", "http://localhost:3001", "http://127.0.0.1:3000.evil.example", "HTTP://127.0.0.1:3000x"]


@pytest.mark.parametrize("origin", FOREIGN)
@pytest.mark.parametrize(
    ("path", "body"),
    [("/api/v1/auth/register", ADA), ("/api/v1/auth/login", ADA), ("/api/v1/auth/logout", None),
     ("/api/v1/queries", {"question": "Where are buyers billed?"})],
)  # fmt: skip
def test_a_state_changing_request_from_a_foreign_origin_is_refused(accounts, origin, path, body) -> None:
    opened: list = []
    web = served(service([reply(BILL)], opened=opened), accounts=accounts)
    response = web.post(path, json=body, headers={"Origin": origin}) if body else web.post(path, headers={"Origin": origin})
    assert response.status_code == 403
    assert response.json() == {"code": "origin_refused", "message": "This request came from a site this server does not serve."}
    assert opened == [] and "set-cookie" not in response.headers


def test_a_refused_origin_changes_nothing(accounts) -> None:
    web = served(accounts=accounts)
    web.post("/api/v1/auth/register", json=ADA, headers={"Origin": "https://evil.example"})
    assert web.post("/api/v1/auth/login", json=ADA).status_code == 401, "the registration was refused, so nothing was made"


@pytest.mark.parametrize("origin", ["http://127.0.0.1:3000", "http://localhost:3000", None])
def test_a_request_from_the_frontend_or_with_no_origin_is_taken(accounts, origin) -> None:
    web = served(accounts=accounts)
    headers = {"Origin": origin} if origin else {}
    assert web.post("/api/v1/auth/register", json=ADA, headers=headers).status_code == 201
    assert web.post("/api/v1/auth/login", json=ADA, headers=headers).status_code == 200
    assert web.post("/api/v1/auth/logout", headers=headers).status_code == 204


def test_a_read_from_a_foreign_origin_is_not_refused_by_the_origin_check(accounts) -> None:
    """Reads change nothing; CORS keeps a foreign page from reading them."""
    web = served(accounts=accounts)
    assert web.get("/api/v1/health", headers={"Origin": "https://evil.example"}).status_code == 200


@pytest.mark.parametrize(
    ("content", "kind"),
    [(b"email=ada%40example.com&password=correct+horse+battery", "application/x-www-form-urlencoded"),
     (b'{"email": "ada@example.com", "password": "correct horse battery"}', "text/plain"),
     (b'--b\r\nContent-Disposition: form-data; name="email"\r\n\r\nada@example.com\r\n--b--\r\n', "multipart/form-data; boundary=b"),
     (b'{"email": "ada@example.com", "password": "correct horse battery"}', None)],
)  # fmt: skip
def test_a_body_that_is_not_declared_json_is_refused(accounts, content, kind) -> None:
    web = served(accounts=accounts)
    web.post("/api/v1/auth/register", json=ADA)
    headers = {"Content-Type": kind} if kind else {}
    response = web.post("/api/v1/auth/login", content=content, headers=headers)
    assert response.status_code == 415
    assert response.json() == {"code": "json_only", "message": "This endpoint takes a JSON body."}
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize("kind", ["application/json", "application/json; charset=utf-8", "Application/JSON"])
def test_a_body_declared_json_is_taken(accounts, kind) -> None:
    web = served(accounts=accounts)
    content = b'{"email": "ada@example.com", "password": "correct horse battery"}'
    assert web.post("/api/v1/auth/register", content=content, headers={"Content-Type": kind}).status_code == 201


def test_cors_names_its_origins_and_never_allows_every_one() -> None:
    with pytest.raises(ValueError, match="never"):
        create_app(Settings(**URLS, frontend_origins="*"))
    with pytest.raises(ValueError, match="never"):
        create_app(Settings(**URLS, frontend_origins="http://localhost:3000, *"))
    web = served()
    response = web.options(
        "/api/v1/auth/login", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"}
    )
    assert response.headers.get("access-control-allow-origin") != "*"
    assert "access-control-allow-origin" not in response.headers


# --------------------------------------------------------------------------
# Nothing logs or returns a password or a token (the owner's ruling 5)
# --------------------------------------------------------------------------


def test_no_log_line_carries_a_password_a_token_or_a_cookie(accounts, caplog) -> None:
    caplog.set_level(logging.DEBUG)

    class Broken:
        def ask(self, question, user_id):
            raise RuntimeError("a fault")

        def history(self, owner):
            return []

    web = served(Broken(), accounts=accounts)
    web.post("/api/v1/auth/register", json=ADA)
    web.post("/api/v1/auth/register", json={**ADA, "password": "short"})
    web.post("/api/v1/auth/login", json={**ADA, "password": "wrong password here"})
    login = web.post("/api/v1/auth/login", json=ADA)
    token = web.cookies.get(COOKIE)
    web.get("/api/v1/auth/me")
    assert web.post("/api/v1/queries", json={"question": "a fault, logged?"}).status_code == 500
    web.post("/api/v1/auth/login", content=b"password=correct+horse+battery", headers={"Content-Type": "text/plain"})
    web.post("/api/v1/auth/logout")
    assert login.status_code == 200 and token
    logged = "\n".join(f"{record.name} {record.getMessage()} {record.args!r}" for record in caplog.records)
    assert "unhandled RuntimeError on POST /api/v1/queries" in logged, "the fault handler's line is the one pinned"
    for secret in (ADA["password"], "wrong password here", "short", token, "correct+horse"):
        assert secret not in logged
    assert "cookie" not in logged.lower()


def test_no_response_returns_a_password_or_a_token(accounts) -> None:
    web = served(accounts=accounts)
    responses = [web.post("/api/v1/auth/register", json=ADA), web.post("/api/v1/auth/login", json=ADA)]
    token = web.cookies.get(COOKIE)
    responses += [web.get("/api/v1/auth/me"), web.post("/api/v1/auth/logout")]
    for response in responses:
        assert ADA["password"] not in response.text and token not in response.text
