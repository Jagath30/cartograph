"""The deployed smoke check (step 12, criterion 8): smoke's API stage run
against the public site, through Vercel's rewrite of /api/v1 to Render.

    docker compose exec -T backend python -m app.smoke_deployed

Run once, alone: the POST line is ONE PAID QUESTION on the production key,
and the run makes one account, smoke-<UTC timestamp>-<random>@cartograph.invalid,
whose password is made at random and never printed. Neither the password
nor the session cookie's value is printed.

  WAKE       GET /health through the rewrite answers. Free hosting sleeps
             after 15 minutes, so it is asked every 5 s for up to 3
             minutes, as the page's notice does; the wait is printed.
  HEADERS    a page carries the six headers of next.config, each value
             exactly.
  SIGNEDOUT  POST /queries without a session is refused, 401. Free.
  SCHEMA     GET /schema returns the stored schema.
  REGISTER   the new account.
  LOGIN      the session cookie is Secure, HttpOnly, SameSite=Lax, Path=/,
             seven days, no Domain.
  FOREIGN    with that session, POST /queries from another site's Origin
             is refused, 403 origin_refused, before the body is read. Free.
  POST       the smoke question is answered, with rows. PAID.
  HISTORY    the account's history is that question, and only it.
  READBACK   the stored trace is identical to the returned one; no rows.
  RERUN      its SQL run again: answered, the same columns and row count.
             No model is called.
  LOGOUT     204, and the cookie cleared.
  REFUSED    with the old cookie, the reads are 401.

Exit: 0 all pass; 1 any fails; 3 the server answered 503 not_ready.
"""

import sys
import time
import urllib.error
import urllib.request

from app.smoke_api import COOKIE, Browser, canonical, session_cookie, smoke_email, smoke_password
from app.smoke_api import FLAGS as LOCAL_FLAGS
from app.smoke_pipeline import NO_KEY_EXIT, QUESTION

SITE = "https://cartograph-nl2sql.vercel.app"
API = SITE + "/api/v1"
FOREIGN_ORIGIN = "https://not-cartograph.example"
FLAGS = LOCAL_FLAGS | {"Secure"}
WAKE_FOR_S = 180
ASK_AGAIN_S = 5

# frontend/next.config.ts SECURITY_HEADERS, each value as sent.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Strict-Transport-Security": "max-age=63072000",
}


def flags_hold(flags: str) -> bool:
    return set(flags.split(", ")) == FLAGS


def headers_missing(headers: dict[str, str]) -> list[str]:
    """The names of the six that are absent or differ, sorted."""
    sent = {name.lower(): value for name, value in headers.items()}
    return sorted(name for name, value in SECURITY_HEADERS.items() if sent.get(name.lower()) != value)


def page_headers(url: str) -> tuple[int, dict[str, str]]:
    request = urllib.request.Request(url, headers={"user-agent": "cartograph-smoke"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, dict(response.headers.items())
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers.items())


def wake(web: Browser) -> tuple[bool, float, str]:
    started = time.monotonic()
    last = ""
    while time.monotonic() - started < WAKE_FOR_S:
        try:
            status, body = web.call("/health")
            if status == 200 and body.get("status") == "ok":
                return True, time.monotonic() - started, ""
            last = f"http {status}"
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last = type(error).__name__
        time.sleep(ASK_AGAIN_S)
    return False, time.monotonic() - started, last


def main() -> int:
    failed = False
    web = Browser(API)

    awake, waited, last = wake(web)
    if awake:
        print(f"WAKE PASS  health answered through the rewrite after {waited:.0f} s")
    else:
        print(f"WAKE FAIL  no answer in {waited:.0f} s; last {last}")
        return 1

    status, headers = page_headers(SITE + "/ask")
    missing = headers_missing(headers)
    if status == 200 and not missing:
        print(f"HEADERS PASS  /ask, http 200, carries the six: {', '.join(SECURITY_HEADERS)}")
    else:
        failed = True
        print(f"HEADERS FAIL  /ask, http {status}; absent or different: {', '.join(missing)}")

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
    if status == 200 and graph.get("nodes") and graph.get("edges"):
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

    status, _ = web.call("/auth/login", account)
    issued = bool(web.set_cookie) and web.set_cookie.startswith(f"{COOKIE}=")
    if status != 200 or not issued:
        print(f"LOGIN FAIL  http {status}; a session cookie was set: {issued}")
        return 1
    web.cookie, flags = session_cookie(web.set_cookie)
    if flags_hold(flags):
        print(f"LOGIN PASS  the session cookie is {flags}")
    else:
        failed = True
        print(f"LOGIN FAIL  the session cookie is {flags}")

    status, foreign = web.call("/queries", {"question": QUESTION}, origin=FOREIGN_ORIGIN)
    if status == 403 and foreign.get("code") == "origin_refused":
        print(f"FOREIGN PASS  POST /queries signed in, from Origin {FOREIGN_ORIGIN}, is refused, 403 origin_refused")
    else:
        failed = True
        print(f"FOREIGN FAIL  http {status}: {canonical(foreign)[:200]}")

    status, asked = web.call("/queries", {"question": QUESTION})
    not_ready = status == 503 and asked.get("code") == "not_ready"
    query_id = asked.get("query_id") if status == 200 else None
    if not_ready:
        print(f"NOT READY  {asked.get('message')}")
    elif status != 200:
        failed = True
        print(f"POST FAIL  http {status}: {canonical(asked)[:300]}")
    else:
        trace = asked["trace"]
        if asked["status"] == "answered" and asked["row_count"] and len(asked["rows"]) == asked["row_count"]:
            print(f"POST PASS  answered; {asked['row_count']} rows; conformance {trace['validation']['conformance']}; "
                  f"${trace['cost_usd']:.6f}; query {query_id}")  # fmt: skip
        else:
            failed = True
            print(f"POST FAIL  status {asked['status']} ({asked.get('message')}); rows {asked.get('row_count')}")

        status, history = web.call("/queries")
        listed = [row["query_id"] for row in history.get("queries", [])] if status == 200 else []
        if listed == [query_id]:
            print("HISTORY PASS  the question is in this account's history, and is all of it")
        else:
            failed = True
            print(f"HISTORY FAIL  http {status}; listed {len(listed)}; the question first: {listed[:1] == [query_id]}")

        status, read = web.call(f"/queries/{query_id}")
        if status == 200 and canonical(read["trace"]) == canonical(trace) and read["rows"] is None:
            print(f"READBACK PASS  the stored trace is identical to the one returned ({len(canonical(trace))} characters); no rows stored")
        else:
            failed = True
            same = status == 200 and canonical(read.get("trace")) == canonical(trace)
            print(f"READBACK FAIL  http {status}; trace identical: {same}; rows returned: {read.get('rows') is not None}")

        status, again = web.call(f"/queries/{query_id}/rerun", method="POST")
        if (status == 200 and again.get("status") == "answered" and again.get("columns") == asked["columns"]
                and again.get("row_count") == asked["row_count"]):  # fmt: skip
            print(f"RERUN PASS  answered again; {again['row_count']} rows, the same columns")
        else:
            failed = True
            print(f"RERUN FAIL  http {status}; status {again.get('status')}; rows {again.get('row_count')} against {asked['row_count']}")

    status, _ = web.call("/auth/logout", method="POST")
    cleared = bool(web.set_cookie) and web.set_cookie.startswith(f'{COOKIE}=""') and "Max-Age=0" in web.set_cookie
    if status == 204 and cleared:
        print("LOGOUT PASS  204, and the cookie cleared")
    else:
        failed = True
        print(f"LOGOUT FAIL  http {status}; the cookie cleared: {cleared}")

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
