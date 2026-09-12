"""The API layer's liveness contract.

Needs no database, no Redis, no network and no environment: the settings
below are constructed in the test, and /health does not touch them. That
is the property the application factory exists to preserve.
"""

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

client = TestClient(
    create_app(
        Settings(
            app_database_url="postgresql://unused/unused",
            warehouse_database_url="postgresql://unused/unused",
            redis_url="redis://unused",
        )
    )
)


def test_health_returns_ok() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_is_only_served_under_the_version_prefix() -> None:
    """IR-01 requires the API to be versioned under a path prefix. If an
    unversioned path also answered, the prefix would be decoration."""
    assert client.get("/health").status_code == 404
