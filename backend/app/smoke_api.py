"""The smoke check's lines for the API (NFR-27; IR-02, IR-03, IR-06,
IR-08, IR-09, FR-27, NFR-10, NFR-13; criterion 12).

    docker compose exec backend python -m app.smoke_api

Asks as a browser would: through the frontend's own origin, whose
next.config rewrites /api/v1 to the backend (step 11), with the session
in a cookie. Each check prints one line that scripts/smoke.sh reads:

  SIGNEDOUT  POST /queries without a session is refused, 401. Free.
  SCHEMA     GET /schema returns the stored schema as nodes and edges.
  REGISTER   a new account, smoke-<UTC timestamp>-<random>@cartograph.invalid,
             the only account this makes. Its password is made at random
             each run and never printed.
  LOGIN      the session cookie, HttpOnly, SameSite=Lax, Path=/, seven
             days, no Domain.
  POST       one fixed development question through POST /queries is
             answered: a query id, rows, and a trace with its narrative,
             at trace version 3 with its linking. TWO OR THREE PAID MODEL
             CALLS (linking, choosing when it runs, generation).
  READBACK   GET /queries/<that id> returns a trace IDENTICAL to the one
             POST returned, compared as canonical JSON, and no rows: the
             trace was stored, whole, and the rows were not (DR-15).
  HISTORY    GET /queries lists that query first, and only this account's.
  LOGOUT     POST /auth/logout is a 204 and clears the cookie.
  REFUSED    with the old cookie, GET /queries and GET /queries/<id> are
             401: the session was revoked.

The question is d4 of eval/dev_questions.yaml, the one `smoke_pipeline`
asks. The POST line is strict, as that one is: it needs "answered".

Exit: 0 all pass; 1 any fails; 3 the server answered 503 `not_ready`
(no key, or no stored schema): a clean skip of the paid lines.
"""

import json
import secrets
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime

from app.smoke_pipeline import NO_KEY_EXIT, QUESTION

# The frontend, as the backend's container reaches it on the compose
# network. Through it, the rewrite is part of what is checked.
API = "http://frontend:3000/api/v1"
COOKIE = "cartograph_session"
FLAGS = {"HttpOnly", "Max-Age=604800", "Path=/", "SameSite=lax"}


def smoke_email() -> str:
    return f"smoke-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{secrets.token_hex(4)}@cartograph.invalid"


def smoke_password() -> str:
    return secrets.token_urlsafe(24)


def session_cookie(header: str) -> tuple[str, str]:
    """The cookie's value, and its attributes as text that holds no value."""
    first, *attributes = [part.strip() for part in header.split(";")]
    return first.split("=", 1)[1], ", ".join(attributes)


def flags_hold(flags: str) -> bool:
    return set(flags.split(", ")) == FLAGS


class Browser:
    """Holds the session cookie, as a browser's jar would, and sends it."""

    def __init__(self) -> None:
        self.cookie: str | None = None
        self.set_cookie: str | None = None

    def call(self, path: str, body: dict | None = None, method: str | None = None) -> tuple[int, dict]:
        headers = {"content-type": "application/json"} if body is not None else {}
        if self.cookie:
            headers["cookie"] = f"{COOKIE}={self.cookie}"
        request = urllib.request.Request(
            API + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
            method=method or ("POST" if body is not None else "GET"),
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                self.set_cookie = response.headers.get("set-cookie")
                raw = response.read()
                return response.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            raw = error.read()
            return error.code, json.loads(raw) if raw else {}


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def main() -> int:
    failed = False
    web = Browser()

    status, refused = web.call("/queries", {"question": QUESTION})
    if status == 401 and refused.get("code") == "not_signed_in":
        print("SIGNEDOUT PASS  POST /queries without a session is refused, 401")
    else:
        failed = True
        print(f"SIGNEDOUT FAIL  http {status}: {canonical(refused)[:200]}")

    status, graph = web.call("/schema")
    if status == 503:
        print(f"NOT READY  {graph.get('message')}")
        return NO_KEY_EXIT
    if status == 200 and graph["nodes"] and graph["edges"]:
        columns = sum(len(node["columns"]) for node in graph["nodes"])
        print(f"SCHEMA PASS  {len(graph['nodes'])} nodes, {len(graph['edges'])} edges, {columns} columns; snapshot {graph['hash'][:12]}")
    else:
        failed = True
        print(f"SCHEMA FAIL  http {status}: {canonical(graph)[:300]}")

    account = {"email": smoke_email(), "password": smoke_password()}
    status, made = web.call("/auth/register", account)
    if status == 201 and made.get("email") == account["email"]:
        print(f"REGISTER PASS  {account['email']}")
    else:
        print(f"REGISTER FAIL  http {status}: {canonical(made)[:200]}")
        return 1

    status, signed = web.call("/auth/login", account)
    if status != 200 or not web.set_cookie or not web.set_cookie.startswith(f"{COOKIE}="):
        print(f"LOGIN FAIL  http {status}; a session cookie was set: {bool(web.set_cookie)}")
        return 1
    web.cookie, flags = session_cookie(web.set_cookie)
    if flags_hold(flags):
        print(f"LOGIN PASS  the session cookie is {flags}")
    else:
        failed = True
        print(f"LOGIN FAIL  the session cookie is {flags}")

    status, asked = web.call("/queries", {"question": QUESTION})
    not_ready = status == 503
    query_id = asked.get("query_id") if status == 200 else None
    if not_ready:
        print(f"NOT READY  {asked.get('message')}")
    elif status != 200:
        failed = True
        print(f"POST FAIL  http {status}: {canonical(asked)[:300]}")
        print("READBACK FAIL  nothing was returned to read back")
        print("HISTORY FAIL  nothing was returned to find")
    else:
        trace = asked["trace"]
        narrative = trace.get("narrative") or {}
        told = all(narrative.get(part) for part in ("found", "route", "not_taken", "sql", "result"))
        linked = trace.get("trace_version") == 3 and bool((trace.get("linking") or {}).get("tables"))
        if asked["status"] == "answered" and asked["row_count"] and len(asked["rows"]) == asked["row_count"] and told and linked:
            print(f"POST PASS  answered; {asked['row_count']} rows; conformance {trace['validation']['conformance']}; "
                  f"${trace['cost_usd']:.6f}; query {asked['query_id']}")  # fmt: skip
        elif asked["status"] == "not_answerable":
            failed = True
            print("POST FAIL  THE MODEL DECLINED the smoke question. This is the model's answer and not a code fault. "
                  f"It was shown: {', '.join((trace.get('generation') or {}).get('tables_shown', []))}")  # fmt: skip
        else:
            failed = True
            print(f"POST FAIL  status {asked['status']} ({asked['message']}); rows {asked['row_count']}; narrative whole: {told}; "
                  f"linked at version 3: {linked}")  # fmt: skip

        # Whatever the outcome, what was returned must be what was stored.
        status, read = web.call(f"/queries/{asked['query_id']}")
        if status == 200 and canonical(read["trace"]) == canonical(trace) and read["rows"] is None:
            print(f"READBACK PASS  the stored trace is identical to the one returned ({len(canonical(trace))} characters); no rows stored")
        else:
            failed = True
            same = status == 200 and canonical(read.get("trace")) == canonical(trace)
            print(f"READBACK FAIL  http {status}; trace identical: {same}; rows returned: {read.get('rows') is not None}")

        status, history = web.call("/queries")
        listed = [row["query_id"] for row in history.get("queries", [])] if status == 200 else []
        if listed == [asked["query_id"]]:
            print("HISTORY PASS  the question is in this account's history, and is all of it")
        else:
            failed = True
            print(f"HISTORY FAIL  http {status}; listed {len(listed)}; the question first: {listed[:1] == [asked['query_id']]}")

    status, _ = web.call("/auth/logout", method="POST")
    cleared = bool(web.set_cookie) and web.set_cookie.startswith(f'{COOKIE}=""') and "Max-Age=0" in web.set_cookie
    if status == 204 and cleared:
        print("LOGOUT PASS  204, and the cookie cleared")
    else:
        failed = True
        print(f"LOGOUT FAIL  http {status}; the cookie cleared: {cleared}")

    # The old cookie, kept as an attacker who copied it would keep it.
    after = [web.call("/queries")[0]]
    if query_id:
        after.append(web.call(f"/queries/{query_id}")[0])
    if all(code == 401 for code in after):
        print(f"REFUSED PASS  with the revoked session, {len(after)} reads are refused, 401")
    else:
        failed = True
        print(f"REFUSED FAIL  with the revoked session: http {after}")

    if not_ready:
        return NO_KEY_EXIT
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
