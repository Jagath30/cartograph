"""HTTP routes: requests in, responses out.

Holds no logic (DD-01). If a decision is being made in this file, it
belongs somewhere else.
"""

from functools import lru_cache
from typing import Annotated
from uuid import UUID

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
    SchemaOut,
    history_out,
    query_out,
    schema_out,
)
from app.auth import COOKIE, AccountsDep, NowDep, UserDep, clear_session, set_session
from app.config import Settings, get_settings
from app.shell.accounts import EmailTaken
from app.shell.probes import check_postgres, check_redis
from app.shell.query_service import QueryService, make_service

router = APIRouter(prefix="/api/v1")

SettingsDep = Annotated[Settings, Depends(get_settings)]


@lru_cache
def get_service() -> QueryService:
    """One for the process. Built on first use and opens nothing until a
    request needs it, so that importing the application needs nothing."""
    return make_service(get_settings().app_database_url)


ServiceDep = Annotated[QueryService, Depends(get_service)]

# What every route below may answer besides its own body (IR-05).
ERRORS = {
    401: {"model": ErrorOut, "description": "`not_signed_in`: the route needs a session, and there is none."},
    403: {"model": ErrorOut, "description": "`origin_refused`: a change asked for by another site."},
    415: {"model": ErrorOut, "description": "`json_only`: a body that is not declared JSON."},
    422: {"model": ErrorOut, "description": "The request was malformed."},
    500: {"model": ErrorOut, "description": "A fault; `trace_not_persisted` when the trace could not be written."},
    503: {"model": ErrorOut, "description": "Something the request needs is not there yet."},
}


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


@router.post("/auth/register", status_code=201, response_model=AccountOut, responses=ERRORS | {409: {"model": ErrorOut}})
def register(body: RegisterIn, accounts: AccountsDep) -> AccountOut:
    """An account. It does not sign in: the page logs in after."""
    try:
        made = accounts.register(body.email, body.password)
    except EmailTaken:
        raise Refused(409, "email_taken", "An account with this email already exists.") from None
    return AccountOut(email=made.email)


@router.post("/auth/login", response_model=AccountOut, responses=ERRORS)
def login(body: LoginIn, response: Response, accounts: AccountsDep, at: NowDep, settings: SettingsDep) -> AccountOut:
    """A session, in the cookie. One answer for every failure."""
    opened = accounts.log_in(body.email, body.password, at)
    if opened is None:
        raise Refused(401, "invalid_credentials", FAILED_LOGIN)
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


@router.post("/queries", response_model=QueryOut, responses=ERRORS)
def ask(body: QuestionIn, service: ServiceDep, user: UserDep) -> QueryOut:
    """IR-02, IR-03. One question, one response, synchronously (D-05).
    EVERY outcome of the pipeline is a 200 with its trace: an answer, a
    decline and a failure alike, the last two with IR-05's code and
    message. The trace is stored before this returns (NFR-13), under the
    user who asked."""
    kept = service.ask(body.question, user.user_id)
    return query_out(kept.document, kept.trace.execution.rows if kept.trace.execution else None)


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


@router.get("/schema", response_model=SchemaOut, responses=ERRORS)
def schema(service: ServiceDep) -> SchemaOut:
    """IR-06: the stored schema as nodes and edges, tables as nodes with
    their columns inside (DD-18)."""
    stored, snapshot = service.schema()
    return schema_out(stored.id, stored.hash, snapshot)
