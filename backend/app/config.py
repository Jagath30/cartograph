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


@lru_cache
def get_settings() -> Settings:
    """Read configuration on first use, not at import time.

    Constructing Settings at module scope would make `import app.main`
    fail without a full environment -- which would put a database
    requirement on tests that have no business needing one.
    """
    return Settings()
