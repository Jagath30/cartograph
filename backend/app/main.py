"""HTTP surface: requests in, responses out.

Holds no logic (DD-01). If a decision is being made in this file, it
belongs somewhere else.
"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.shell.probes import check_postgres, check_redis

app = FastAPI(title="Cartograph", version="0.1.0")

API = "/api/v1"


@app.get(f"{API}/health")
async def health() -> dict[str, str]:
    """Liveness. Touches nothing on purpose -- this is what the container
    healthcheck polls, and it must not fail because a dependency is down."""
    return {"status": "ok"}


@app.get(f"{API}/ready")
async def ready() -> JSONResponse:
    """Readiness. Checks both database connections separately, with their
    own credentials, plus Redis."""
    settings = get_settings()

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
