"""HTTP routes: requests in, responses out.

Holds no logic (DD-01). If a decision is being made in this file, it
belongs somewhere else.
"""

from functools import lru_cache
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.api_models import ErrorOut, QueryOut, QuestionIn, SchemaOut, query_out, schema_out
from app.config import Settings, get_settings
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


@router.post("/queries", response_model=QueryOut, responses=ERRORS)
def ask(body: QuestionIn, service: ServiceDep) -> QueryOut:
    """IR-02, IR-03. One question, one response, synchronously (D-05).
    EVERY outcome of the pipeline is a 200 with its trace: an answer, a
    decline and a failure alike, the last two with IR-05's code and
    message. The trace is stored before this returns (NFR-13)."""
    kept = service.ask(body.question)
    return query_out(kept.document, kept.trace.execution.rows if kept.trace.execution else None)


@router.get("/queries/{query_id}", response_model=QueryOut, responses=ERRORS | {404: {"model": ErrorOut}})
def fetch(query_id: UUID, service: ServiceDep) -> QueryOut:
    """One stored query with its trace. The rows are not stored (DR-15),
    so `rows` is null and the trace's row count says how many there were."""
    document = service.fetch(query_id)
    if document is None:
        raise HTTPException(status_code=404, detail=f"no query {query_id} is stored")
    return query_out(document, None)


@router.get("/schema", response_model=SchemaOut, responses=ERRORS)
def schema(service: ServiceDep) -> SchemaOut:
    """IR-06: the stored schema as nodes and edges, tables as nodes with
    their columns inside (DD-18)."""
    stored, snapshot = service.schema()
    return schema_out(stored.id, stored.hash, snapshot)
