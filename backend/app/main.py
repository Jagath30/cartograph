"""Application construction.

Nothing in this module runs at import time. `create_app` is called by
uvicorn through --factory, and by tests with settings of their choosing,
which is what keeps `import app.main` free of any configuration
requirement -- and keeps that property visible rather than propped up by
test scaffolding.
"""

import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.api import get_service, router
from app.api_models import ErrorOut, Refused
from app.auth import RequestGuard
from app.config import Settings, get_settings
from app.orchestrator import TraceNotPersisted
from app.shell.query_service import NotReady

log = logging.getLogger("cartograph.api")


def _error(status: int, **fields) -> JSONResponse:
    return JSONResponse(status_code=status, content=ErrorOut(**fields).model_dump(mode="json", exclude_none=True))


def _errors(app: FastAPI) -> None:
    """IR-05: every error body is a code and a message. 4xx is a request
    that was malformed or asked for something that is not there; 5xx is a
    fault. An outcome of the pipeline is neither: it is a 200 with its
    trace."""

    @app.exception_handler(RequestValidationError)
    async def malformed(request: Request, error: RequestValidationError) -> JSONResponse:
        # What was sent is not echoed back: only where it was wrong and why.
        detail = [{"where": ".".join(str(part) for part in item["loc"]), "why": item["msg"]} for item in error.errors()]
        return _error(422, code="invalid_request", message="The request was not in the form this endpoint takes.", detail=detail)

    @app.exception_handler(HTTPException)
    async def refused(request: Request, error: HTTPException) -> JSONResponse:
        code = "not_found" if error.status_code == 404 else "invalid_request"
        return _error(error.status_code, code=code, message=str(error.detail))

    @app.exception_handler(Refused)
    async def said_no(request: Request, error: Refused) -> JSONResponse:
        return _error(error.status, code=error.code, message=error.message)

    @app.exception_handler(NotReady)
    async def not_ready(request: Request, error: NotReady) -> JSONResponse:
        return _error(503, code="not_ready", message=str(error))

    @app.exception_handler(TraceNotPersisted)
    async def not_persisted(request: Request, error: TraceNotPersisted) -> JSONResponse:
        return _error(
            500, code="trace_not_persisted", query_id=error.query_id,
            message="The question was run and its trace could not be stored, so its answer is not returned. "
            "The trace is in the server's log under this query id.",
        )  # fmt: skip

    @app.exception_handler(Exception)
    async def fault(request: Request, error: Exception) -> JSONResponse:
        # The class and never the words: an error's text can carry anything.
        log.error("unhandled %s on %s %s", type(error).__name__, request.method, request.url.path)
        return _error(500, code="internal_error", message="The server failed while handling this request.")


@asynccontextmanager
async def _opened_in_the_background(app: FastAPI) -> AsyncIterator[None]:
    """Once the server has started, the pipeline is opened in a thread of
    its own (step 12, the owner's ruling 3): a host waking from sleep
    then opens it as part of waking, and the first question does not wait
    for it unless it arrives first. Serving never waits for it, and it
    never stops the server: what is not ready is said to the question."""
    try:
        service = app.dependency_overrides.get(get_service, get_service)()
    except Exception as error:
        log.error("the pipeline was not opened at start: %s", type(error).__name__)
    else:
        threading.Thread(target=service.warm, name="open-pipeline", daemon=True).start()
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()

    app = FastAPI(title="Cartograph", version="0.1.0", lifespan=_opened_in_the_background)

    origins = resolved.allowed_origins()
    # Inside CORS: a change from another site is refused before any route
    # is reached (step 11, T-08).
    app.add_middleware(RequestGuard, origins=origins)
    # Named origins, never "*" (allowed_origins refuses it). The browser
    # reaches the API through the frontend's own origin, so CORS is the
    # second line, not the first.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)
    _errors(app)

    if settings is not None:
        # Explicitly supplied settings must reach the route handlers too,
        # not only the middleware above.
        app.dependency_overrides[get_settings] = lambda: resolved

    return app
