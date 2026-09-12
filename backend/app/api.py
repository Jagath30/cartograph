"""HTTP routes: requests in, responses out.

Holds no logic (DD-01). If a decision is being made in this file, it
belongs somewhere else.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.config import Settings, get_settings
from app.shell.probes import check_postgres, check_redis

router = APIRouter(prefix="/api/v1")

SettingsDep = Annotated[Settings, Depends(get_settings)]


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
