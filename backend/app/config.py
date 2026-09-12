"""Runtime configuration, read from the environment (IR-15).

Nothing about a specific database is compiled in: both connection URLs
arrive as environment variables, and the two are separate values so that
no component can accidentally hold the wrong one (DD-02, rule 2).
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    app_database_url: str
    warehouse_database_url: str
    redis_url: str

    # Comma-separated rather than a list: pydantic-settings expects JSON for
    # complex types, and a plain string with an explicit split is one less
    # thing to get wrong in a deployment environment variable.
    frontend_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.frontend_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Read configuration on first use, not at import time.

    Constructing Settings at module scope would make `import app.main`
    fail without a full environment -- which would put a database
    requirement on tests that have no business needing one.
    """
    return Settings()
