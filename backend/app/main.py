"""Application construction.

Nothing in this module runs at import time. `create_app` is called by
uvicorn through --factory, and by tests with settings of their choosing,
which is what keeps `import app.main` free of any configuration
requirement -- and keeps that property visible rather than propped up by
test scaffolding.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import router
from app.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()

    app = FastAPI(title="Cartograph", version="0.1.0")

    # Named origins, never "*". A wildcard is incompatible with credentialed
    # requests, so allowing it now would have to be undone at step 11 when
    # sessions arrive (T-08).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.allowed_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)

    if settings is not None:
        # Explicitly supplied settings must reach the route handlers too,
        # not only the middleware above.
        app.dependency_overrides[get_settings] = lambda: resolved

    return app
