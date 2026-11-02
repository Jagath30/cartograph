"""HTTP routes: requests in, responses out.

Holds no logic (DD-01). If a decision is being made in this file, it
belongs somewhere else.
"""

from collections.abc import Callable
from functools import lru_cache
from typing import Annotated
from uuid import UUID

import psycopg
import redis
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from app.api_models import (
    AccountOut,
    ErrorOut,
    HistoryOut,
    LoginIn,
    QueryOut,
    QuestionIn,
    Refused,
    RegisterIn,
    RerunOut,
    SchemaOut,
    history_out,
    query_out,
    rerun_out,
    schema_out,
)
from app.auth import COOKIE, AccountsDep, NowDep, UserDep, admin_allowed, clear_session, set_session
from app.config import Settings, get_settings
from app.core.limits import EVERYONE, LOGIN_CEILING, LOGIN_FAILURES, QUESTIONS, REGISTRATIONS, RERUNS, email_subject, user_subject
from app.shell.counters import Counters, Reached
from app.shell.reingest import SchemaRead, read_schema
from app.shell.accounts import EmailTaken
from app.shell.probes import check_postgres, check_redis
from app.core.budget import Allowance
from app.shell.query_service import NothingToRerun, NotReady, OverAllowance, QueryService, make_service

router = APIRouter(prefix="/api/v1")

SettingsDep = Annotated[Settings, Depends(get_settings)]


@lru_cache
def get_service() -> QueryService:
    """One for the process. Built on first use and opens nothing until a
    request needs it, so that importing the application needs nothing."""
    settings = get_settings()
    allowance = Allowance(settings.demo_monthly_budget_usd, settings.daily_questions_per_user)
    return make_service(settings.app_database_url, settings.warehouse_database_url, allowance, settings.redis_url)


ServiceDep = Annotated[QueryService, Depends(get_service)]


@lru_cache
def get_counters() -> Counters:
    """The rate limits' counters, in Redis (FR-36, step 14). Connects on
    first use, never at import."""
    return Counters.from_url(get_settings().redis_url)


CountersDep = Annotated[Counters, Depends(get_counters)]


def limited(reached: Reached | None) -> None:
    """A limit reached is 429, with its own code, Retry-After, and words
    saying which limit and when it lifts (app.core.limits)."""
    if reached is not None:
        limit, seconds = reached.limit, reached.retry_after
        raise Refused(429, limit.code, limit.message(seconds), {"Retry-After": str(seconds)})

# What every route below may answer besides its own body (IR-05).
ERRORS = {
    401: {"model": ErrorOut, "description": "`not_signed_in`: the route needs a session, and there is none."},
    403: {"model": ErrorOut, "description": "`origin_refused`: a change asked for by another site."},
    415: {"model": ErrorOut, "description": "`json_only`: a body that is not declared JSON."},
    422: {"model": ErrorOut, "description": "The request was malformed."},
    500: {"model": ErrorOut, "description": "A fault; `trace_not_persisted` when the trace could not be written."},
    503: {"model": ErrorOut, "description": "Something the request needs is not there yet."},
}
# What asking may answer besides (NFR-25, step 12).
OVER_ALLOWANCE = {
    429: {"model": ErrorOut, "description": "`daily_limit`: this account has asked its questions for the UTC day."},
    503: {"model": ErrorOut, "description": "`budget_spent`: the demo's budget for the month is spent; or `not_ready`."},
}
OVER_ALLOWANCE_STATUS = {"budget_spent": 503, "daily_limit": 429}
# The rate limits (FR-36, step 14). Each carries Retry-After.
LOGIN_LIMITED = {429: {"model": ErrorOut, "description": "`login_limit` or `login_ceiling`, with Retry-After."}}
REGISTER_LIMITED = {429: {"model": ErrorOut, "description": "`register_limit`, with Retry-After."}}
ASK_LIMITED = {429: {"model": ErrorOut, "description": "`daily_limit`; or `minute_limit`, with Retry-After."}}
RERUN_LIMITED = {429: {"model": ErrorOut, "description": "`rerun_limit`, with Retry-After."}}


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness. Touches nothing on purpose -- this is what the container
    healthcheck polls, and it must not fail because a dependency is down."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(settings: SettingsDep) -> JSONResponse:
    """Readiness. Checks both database connections separately, with their
    own credentials, plus Redis."""
    app_ok, app_detail = await check_postgres(settings.app_database_url)
    warehouse_ok, warehouse_detail = await check_postgres(settings.warehouse_database_url)
    redis_ok, redis_detail = await check_redis(settings.redis_url)

    checks = {
        "app_database": {"ok": app_ok, "detail": app_detail},
        "warehouse_database": {"ok": warehouse_ok, "detail": warehouse_detail},
        "redis": {"ok": redis_ok, "detail": redis_detail},
    }
    all_ok = all(check["ok"] for check in checks.values())

    return JSONResponse(
        status_code=200 if all_ok else 503,
        content={"status": "ready" if all_ok else "not_ready", "checks": checks},
    )


# --------------------------------------------------------------------------
# Accounts (IR-09)
# --------------------------------------------------------------------------

FAILED_LOGIN = "The email or password is not right."


@router.post("/auth/register", status_code=201, response_model=AccountOut, responses=ERRORS | {409: {"model": ErrorOut}} | REGISTER_LIMITED)
def register(body: RegisterIn, accounts: AccountsDep, counters: CountersDep) -> AccountOut:
    """An account. It does not sign in: the page logs in after. Every
    attempt counts towards the hour's 30."""
    limited(counters.count(REGISTRATIONS, EVERYONE))
    try:
        made = accounts.register(body.email, body.password)
    except EmailTaken:
        raise Refused(409, "email_taken", "An account with this email already exists.") from None
    return AccountOut(email=made.email)


@router.post("/auth/login", response_model=AccountOut, responses=ERRORS | LOGIN_LIMITED)
def login(
    body: LoginIn, response: Response, accounts: AccountsDep, at: NowDep, settings: SettingsDep, counters: CountersDep
) -> AccountOut:
    """A session, in the cookie. One answer for every failure. Every
    attempt counts towards the hour's ceiling; an email at its five
    failures is refused before any password is hashed, the same for an
    email no account holds; a success clears its failures."""
    limited(counters.count(LOGIN_CEILING, EVERYONE))
    subject = email_subject(body.email)
    limited(counters.reached(LOGIN_FAILURES, subject))
    opened = accounts.log_in(body.email, body.password, at)
    if opened is None:
        counters.count(LOGIN_FAILURES, subject)
        raise Refused(401, "invalid_credentials", FAILED_LOGIN)
    counters.clear(LOGIN_FAILURES, subject)
    token, signed = opened
    set_session(response, token, settings)
    return AccountOut(email=signed.email)


@router.post("/auth/logout", status_code=204, responses=ERRORS)
def logout(request: Request, accounts: AccountsDep, at: NowDep, settings: SettingsDep) -> Response:
    """The session revoked and the cookie cleared; a 204 with or without
    one."""
    token = request.cookies.get(COOKIE)
    if token:
        accounts.log_out(token, at)
    response = Response(status_code=204)
    clear_session(response, settings)
    return response


@router.get("/auth/me", response_model=AccountOut, responses=ERRORS)
def me(user: UserDep) -> AccountOut:
    return AccountOut(email=user.email)


# --------------------------------------------------------------------------
# Questions and history (IR-02, IR-03, IR-08)
# --------------------------------------------------------------------------


@router.post("/queries", response_model=QueryOut, responses=ERRORS | OVER_ALLOWANCE | ASK_LIMITED)
def ask(body: QuestionIn, service: ServiceDep, user: UserDep, at: NowDep, counters: CountersDep) -> QueryOut:
    """IR-02, IR-03. One question, one response, synchronously (D-05).
    EVERY outcome of the pipeline is a 200 with its trace: an answer, a
    decline and a failure alike, the last two with IR-05's code and
    message. The trace is stored before this returns (NFR-13), under the
    user who asked. A question past the month's budget or the user's day
    is not run (NFR-25): 503 or 429, and nothing is stored. Every ask
    counts towards the minute's 10 (FR-36). A repeated question may be
    served from the answer cache (FR-35): stored under this user at
    version 4, costing 0, over the budget and the day as well."""
    limited(counters.count(QUESTIONS, user_subject(user.user_id)))
    try:
        asked = service.answer(body.question, user.user_id, at)
    except OverAllowance as over:
        raise Refused(OVER_ALLOWANCE_STATUS[over.refused], over.refused, over.message) from None
    return query_out(asked.document, asked.rows)


@router.get("/queries", response_model=HistoryOut, responses=ERRORS)
def history(service: ServiceDep, user: UserDep) -> HistoryOut:
    """IR-08: the caller's own queries, newest first, the newest 100."""
    return history_out(service.history(user.user_id))


@router.get("/queries/{query_id}", response_model=QueryOut, responses=ERRORS | {404: {"model": ErrorOut}})
def fetch(query_id: UUID, service: ServiceDep, user: UserDep) -> QueryOut:
    """One of the caller's stored queries with its trace. Anyone else's is
    404, as one that does not exist: never 403. The rows are not stored
    (DR-15), so `rows` is null and the trace's row count says how many
    there were."""
    document = service.fetch(query_id, user.user_id)
    if document is None:
        raise HTTPException(status_code=404, detail=f"no query {query_id} is stored")
    return query_out(document, None)


@router.post(
    "/queries/{query_id}/rerun", response_model=RerunOut,
    responses=ERRORS | {404: {"model": ErrorOut}, 409: {"model": ErrorOut}} | RERUN_LIMITED,
)  # fmt: skip
def rerun(query_id: UUID, service: ServiceDep, user: UserDep, at: NowDep, counters: CountersDep) -> RerunOut:
    """Item 88: one of the caller's stored answers, its SQL validated
    again and run now. No model is called; the rows are returned and not
    stored. Anyone else's is 404. Every rerun counts towards the hour's 30."""
    limited(counters.count(RERUNS, user_subject(user.user_id)))
    try:
        done = service.rerun(query_id, user.user_id)
    except NothingToRerun:
        raise Refused(409, "nothing_to_rerun", "This answer has no SQL to run again.") from None
    if done is None:
        raise HTTPException(status_code=404, detail=f"no query {query_id} is stored")
    return rerun_out(query_id, at, done)


@router.get("/schema", response_model=SchemaOut, responses=ERRORS)
def schema(service: ServiceDep) -> SchemaOut:
    """IR-06: the stored schema as nodes and edges, tables as nodes with
    their columns inside (DD-18)."""
    stored, snapshot = service.schema()
    return schema_out(stored.id, stored.hash, snapshot)


# --------------------------------------------------------------------------
# Re-ingest (IR-07, FR-05; step 14, the owner's ruling 2)
# --------------------------------------------------------------------------

REINGEST_KEY = "cartograph:admin:reingest"
REINGEST_EVERY_SECONDS = 10 * 60
SCHEMA_CHANGED = (
    "The warehouse's schema is not the one this deployment's retrieval was calibrated on, so it cannot be served. "
    "A changed schema needs a new calibration first, an offline step (python -m app.ingest_schema, then python -m "
    "app.calibrate). Nothing was stored."
)


def get_schema_reader() -> Callable[[], SchemaRead]:
    return lambda: read_schema(get_settings())


@router.post("/admin/reingest", include_in_schema=False)
def reingest(
    request: Request, settings: SettingsDep, service: ServiceDep, counters: CountersDep,
    read: Annotated[Callable[[], SchemaRead], Depends(get_schema_reader)],
) -> dict:  # fmt: skip
    """Exists only for a request carrying ADMIN_TOKEN as its bearer token;
    to any other, it is 404, as a path never routed. Once in 10 minutes;
    without Redis, never. Reads the live catalog and the overlay, and
    compares the snapshot's hash with the calibration's: the same is 200
    `unchanged`, and the pipeline is reopened only if it is not open; a
    different one is 409 `schema_changed`. Nothing is stored, and no model
    or embedder is called, on either path."""
    if not admin_allowed(settings.admin_token, request.headers.get("authorization")):
        raise HTTPException(status_code=404, detail="Not Found")
    try:
        held = counters.claim(REINGEST_KEY, REINGEST_EVERY_SECONDS)
    except redis.RedisError:
        raise Refused(503, "not_ready", "Re-ingest needs Redis, which did not answer, so nothing was done.") from None
    if held is not None:
        message = f"Re-ingest runs at most once in 10 minutes. It can run again in {held} seconds."
        raise Refused(429, "reingest_limit", message, {"Retry-After": str(held)})
    try:
        schema = read()
    except psycopg.OperationalError:
        raise NotReady("the warehouse could not be reached, so its schema could not be read") from None
    if not schema.unchanged:
        raise Refused(409, "schema_changed", SCHEMA_CHANGED)
    reopened = service.reopen_if_not_ready()
    return {"status": "unchanged", "hash": schema.live, "pipeline_reopened": reopened}

